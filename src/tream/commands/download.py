import asyncio
import os
from pathlib import Path
import sys
from typing import Any
import httpx
import questionary
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from tream.core.config import load_config
from tream.core.search import search_all
from tream.core.series import VIDEO_EXTENSIONS
from tream.core.torrserver import TorrServerClient, find_torrserver_binary, spawn_torrserver, stop_torrserver
from tream.utils.cache import format_bytes, parse_buffer_size
from tream.utils.logging import get_console, spinner, status
from tream.utils.picker import interactive_pick_result, pick_by_index

_CHUNK_SIZE = 65536


def _looks_like_path(token: str) -> bool:
    """Heuristic: does this argument look like a filesystem path rather than a query word?"""
    if not token:
        return False
    if token.startswith(("~", "./", "../", ".\\", "..\\")):
        return True
    if os.sep in token:
        return True
    if os.altsep and os.altsep in token:
        return True
    return Path(token).expanduser().is_dir()


def resolve_download_target(
    args: list[str],
    explicit: str | Path | None = None,
) -> tuple[str, Path]:
    """Split positional args into (search_query, target_folder).

    The last positional argument is treated as the target folder only when it
    clearly looks like a path (e.g. ``./isos``, ``/mnt/media``, an existing
    directory) or when an explicit ``--output`` folder was given. This keeps
    multi-word queries such as ``tream -d dune part two`` intact.
    """
    if explicit is not None:
        return " ".join(args).strip(), Path(explicit).expanduser()

    if len(args) >= 2 and _looks_like_path(args[-1]):
        return " ".join(args[:-1]).strip(), Path(args[-1]).expanduser()

    return " ".join(args).strip(), Path.cwd()


def _partial_path(dest: Path) -> Path:
    """Return the .part path used for interrupted downloads."""
    return dest.with_name(dest.name + ".part")


def _sort_files_for_selection(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Video files first (largest first), then remaining files alphabetically."""

    def key(f: dict[str, Any]) -> tuple[int, int, str]:
        path = str(f.get("path") or f.get("Path") or "")
        is_video = 0 if Path(path).suffix.lower() in VIDEO_EXTENSIONS else 1
        return (is_video, -int(f.get("length", f.get("Length", 0))), path.lower())

    return sorted(files, key=key)


def _select_files(files: list[dict[str, Any]], download_all: bool) -> list[dict[str, Any]] | None:
    """Let the user choose which files to download. Returns None when cancelled."""
    if len(files) <= 1 or download_all:
        return files

    if not sys.stdin.isatty():
        status(f"no interactive terminal; downloading all {len(files)} files")
        return files

    ordered = _sort_files_for_selection(files)
    while True:
        try:
            action = questionary.select(
                f"{len(files)} files in this torrent:",
                choices=[
                    questionary.Choice(title=f"[Download all {len(files)} files]", value="ALL"),
                    questionary.Choice(title="[Choose specific files...]", value="PICK"),
                    questionary.Choice(title="[Cancel download]", value=None),
                ],
            ).ask()
        except (KeyboardInterrupt, EOFError):
            return None

        if action is None:
            return None
        if action == "ALL":
            return files

        choices = []
        for f in ordered:
            rel = str(f.get("path") or f.get("Path") or "Unknown")
            size = int(f.get("length", f.get("Length", 0)))
            choices.append(
                questionary.Choice(title=f"{rel} ({format_bytes(size)})", value=f, checked=True)
            )

        try:
            picked = questionary.checkbox(
                "Select files (space to toggle, 'a' to select all, enter to confirm):",
                choices=choices,
            ).ask()
        except (KeyboardInterrupt, EOFError):
            picked = None

        if picked:
            return list(picked)


def _download_file(
    ts_client: TorrServerClient,
    infohash: str,
    file_info: dict[str, Any],
    dest_root: Path,
    overwrite: bool = False,
) -> tuple[str, int]:
    """Download one torrent file with resume support.

    Returns (status, bytes_written) where status is one of
    "downloaded", "resumed", "skipped", or "failed".
    """
    console = get_console()
    file_id = int(file_info.get("id", file_info.get("Id", 0)))
    rel_path = str(file_info.get("path") or file_info.get("Path") or f"file_{file_id}.dat")
    length = int(file_info.get("length", file_info.get("Length", 0)))

    dest = dest_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _partial_path(dest)

    if overwrite:
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    elif dest.is_file() and length > 0 and dest.stat().st_size == length:
        console.print(f"[yellow]skip[/yellow]     {rel_path} [dim](already downloaded)[/dim]")
        return "skipped", 0

    existing = part.stat().st_size if part.is_file() else 0
    if length > 0 and existing >= length:
        part.replace(dest)
        console.print(f"[yellow]skip[/yellow]     {rel_path} [dim](completed earlier)[/dim]")
        return "skipped", 0

    if length > 0 and 0 < existing < length:
        percent = existing / length * 100
        console.print(f"[cyan]resume[/cyan]   {rel_path} [dim]({percent:.0f}% already downloaded)[/dim]")

    url = ts_client.get_stream_url(infohash, file_id=file_id, filename=dest.name)
    headers = {"Range": f"bytes={existing}-"} if existing > 0 else {}
    timeout = httpx.Timeout(30.0, connect=10.0, read=60.0)

    progress = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    initial = existing
    written = existing
    resumed = existing > 0
    label = f"{'resuming' if resumed else 'downloading'} {Path(rel_path).name}"
    task_id = progress.add_task(label, total=length if length > 0 else None, completed=existing)

    try:
        with progress:
            with httpx.stream(
                "GET", url, timeout=timeout, headers=headers, follow_redirects=True
            ) as resp:
                if resp.status_code == 416:
                    # Requested range is already complete
                    part.replace(dest)
                    return "skipped", 0

                resp.raise_for_status()

                if initial and resp.status_code != 206:
                    # Server ignored the Range header; restart from scratch
                    initial = 0
                    written = 0
                    resumed = False
                    progress.update(task_id, completed=0, description=f"downloading {Path(rel_path).name}")
                    mode = "wb"
                else:
                    mode = "ab" if initial else "wb"

                if length <= 0:
                    content_length = resp.headers.get("content-length")
                    if content_length and content_length.isdigit():
                        progress.update(task_id, total=int(content_length) + initial)

                with open(part, mode) as out_fp:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK_SIZE):
                        if not chunk:
                            continue
                        out_fp.write(chunk)
                        written += len(chunk)
                        progress.update(task_id, completed=written)
    except KeyboardInterrupt:
        raise
    except Exception as err:
        console.print(f"[red]failed[/red]   {rel_path}: {err}")
        return "failed", max(0, written - initial)

    part.replace(dest)
    return ("resumed" if resumed else "downloaded"), max(0, written - initial)


def run_download(
    query: str,
    target_folder: Path | None = None,
    buffer_override: str | None = None,
    all_files: bool = False,
    overwrite: bool = False,
    verbose: bool = False,
    pick_index: int | None = None,
    providers: list[str] | None = None,
    min_seeders: int | None = None,
) -> int:
    """Search, pick, and download torrent files to the specified folder with progress."""
    cfg = load_config()
    target_path = Path(target_folder) if target_folder else Path.cwd()
    target_path = target_path.expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    status("searching indexes...")
    results = asyncio.run(
        search_all(query, cfg, providers_override=providers, min_seeders=min_seeders)
    )

    if not results:
        status("no results found")
        return 1

    if pick_index is not None:
        try:
            chosen = pick_by_index(results, pick_index)
        except IndexError as err:
            status(f"{err}")
            return 1
        status(f"selected: {chosen.title} [{chosen.quality}] S:{chosen.seeders}")
    else:
        chosen = interactive_pick_result(query, results, page_size=10)
        if not chosen:
            return 0

    buf_str = buffer_override or cfg.get("playback", {}).get("buffer", "256M")
    try:
        buf_bytes = parse_buffer_size(buf_str)
    except ValueError as err:
        status(f"invalid buffer size: {err}")
        return 1

    port = cfg.get("general", {}).get("torrserver_port", 8090)
    torrserver_bin = find_torrserver_binary(cfg.get("general", {}).get("torrserver_binary", "torrserver"))

    with spinner("starting torrserver..."):
        try:
            spawn_torrserver(port=port, binary_path=torrserver_bin)
        except Exception as err:
            status(f"failed to start torrserver: {err}")
            return 1

    try:
        return _download_from_torrserver(
            chosen_magnet=chosen.magnet,
            chosen_title=chosen.title,
            chosen_infohash=chosen.infohash,
            target_path=target_path,
            cfg=cfg,
            buf_bytes=buf_bytes,
            port=port,
            all_files=all_files,
            overwrite=overwrite,
        )
    finally:
        if stop_torrserver():
            status("cleaning up...")


def download_magnet(
    magnet_or_hash: str,
    title: str = "",
    target_folder: Path | None = None,
    buffer_override: str | None = None,
    all_files: bool = True,
    overwrite: bool = False,
) -> int:
    """Download a magnet URI or infohash directly without searching."""
    from tream.utils.magnet import parse_magnet

    try:
        info = parse_magnet(magnet_or_hash)
    except ValueError as err:
        status(f"invalid magnet/infohash: {err}")
        return 1

    cfg = load_config()
    target_path = Path(target_folder) if target_folder else Path.cwd()
    target_path = target_path.expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    buf_str = buffer_override or cfg.get("playback", {}).get("buffer", "256M")
    try:
        buf_bytes = parse_buffer_size(buf_str)
    except ValueError as err:
        status(f"invalid buffer size: {err}")
        return 1

    port = cfg.get("general", {}).get("torrserver_port", 8090)
    torrserver_bin = find_torrserver_binary(cfg.get("general", {}).get("torrserver_binary", "torrserver"))

    with spinner("starting torrserver..."):
        try:
            spawn_torrserver(port=port, binary_path=torrserver_bin)
        except Exception as err:
            status(f"failed to start torrserver: {err}")
            return 1

    try:
        return _download_from_torrserver(
            chosen_magnet=info.raw_uri,
            chosen_title=title.strip() or info.name or info.infohash,
            chosen_infohash=info.infohash,
            target_path=target_path,
            cfg=cfg,
            buf_bytes=buf_bytes,
            port=port,
            all_files=all_files,
            overwrite=overwrite,
        )
    finally:
        if stop_torrserver():
            status("cleaning up...")


def _download_from_torrserver(
    chosen_magnet: str,
    chosen_title: str,
    chosen_infohash: str,
    target_path: Path,
    cfg: dict[str, Any],
    buf_bytes: int,
    port: int,
    all_files: bool,
    overwrite: bool,
) -> int:
    ts_client = TorrServerClient(port=port)
    ts_client.configure_settings(
        buffer_bytes=buf_bytes,
        preload_cache=cfg.get("playback", {}).get("preload_cache", 10),
        connections_limit=cfg.get("playback", {}).get("connections_limit", 800),
        enable_dht=cfg.get("playback", {}).get("enable_dht", True),
    )

    with spinner("fetching torrent metadata..."):
        try:
            ts_client.add_torrent(chosen_magnet, chosen_title)
            files = ts_client.wait_for_files(chosen_infohash, timeout=40.0)
        except TimeoutError:
            status("metadata timeout; torrent has no active seeders")
            return 1
        except Exception as err:
            status(f"failed to load torrent: {err}")
            return 1

    if not files:
        status("no files found in torrent")
        return 1

    to_download = _select_files(files, all_files)
    if not to_download:
        return 0

    totals: dict[str, int] = {"downloaded": 0, "resumed": 0, "skipped": 0, "failed": 0}
    bytes_written = 0
    status(f"downloading {len(to_download)} file(s) to {target_path}")

    for f in to_download:
        try:
            result, written = _download_file(
                ts_client, chosen_infohash, f, target_path, overwrite=overwrite
            )
        except KeyboardInterrupt:
            status("\ndownload interrupted — partial files are kept as .part and will resume next run")
            raise
        totals[result] = totals.get(result, 0) + 1
        bytes_written += written

    ok_count = totals["downloaded"] + totals["resumed"] + totals["skipped"]
    if totals["failed"]:
        status(
            f"{totals['failed']} of {len(to_download)} file(s) failed "
            f"({format_bytes(bytes_written)} transferred) — re-run the same command to resume"
        )
        return 1
    status(f"saved to {target_path} ({ok_count} file(s), {format_bytes(bytes_written)} transferred)")
    return 0
