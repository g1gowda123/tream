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
from tream.core.torrserver import TorrServerClient, find_torrserver_binary, spawn_torrserver, stop_torrserver
from tream.providers.base import SearchResult
from tream.utils.cache import format_bytes, parse_buffer_size
from tream.utils.logging import get_console, get_logger, spinner, status


def _pick_search_result(results: list[SearchResult]) -> SearchResult | None:
    choices: list[questionary.Choice] = []
    for r in results:
        label = f"[{r.provider}] {r.title} | {r.formatted_size} | S:{r.seeders} L:{r.leechers} [{r.quality}]"
        choices.append(questionary.Choice(title=label, value=r))
    choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        return questionary.select("Select result to download:", choices=choices).ask()
    except (KeyboardInterrupt, EOFError):
        return None


def run_download(
    query: str,
    target_folder: Path,
    buffer_override: str | None = None,
) -> None:
    """Search, pick, and download torrent files to the specified folder with progress."""
    cfg = load_config()
    target_path = Path(target_folder).expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    status("searching indexes...")
    results = asyncio.run(search_all(query, cfg))

    if not results:
        status("no results found")
        return

    status(f"found {len(results)} results")
    chosen = _pick_search_result(results)
    if not chosen:
        return

    buf_str = buffer_override or cfg.get("playback", {}).get("buffer", "256M")
    try:
        buf_bytes = parse_buffer_size(buf_str)
    except ValueError as err:
        status(f"invalid buffer size: {err}")
        return

    port = cfg.get("general", {}).get("torrserver_port", 8090)
    torrserver_bin = find_torrserver_binary(cfg.get("general", {}).get("torrserver_binary", "torrserver"))

    with spinner("starting torrserver..."):
        spawn_torrserver(port=port, binary_path=torrserver_bin)

    ts_client = TorrServerClient(port=port)
    ts_client.configure_settings(
        buffer_bytes=buf_bytes,
        preload_cache=cfg.get("playback", {}).get("preload_cache", 30),
        connections_limit=cfg.get("playback", {}).get("connections_limit", 800),
        enable_dht=cfg.get("playback", {}).get("enable_dht", True),
    )

    with spinner("fetching torrent metadata..."):
        ts_client.add_torrent(chosen.magnet, chosen.title)
        try:
            files = ts_client.wait_for_files(chosen.infohash, timeout=40.0)
        except TimeoutError:
            status("metadata timeout; torrent has no active seeders")
            return

    if not files:
        status("no files found in torrent")
        return

    # If multiple files, ask whether to download all or pick one
    to_download: list[dict[str, Any]] = []
    if len(files) == 1:
        to_download = files
    else:
        file_choices = [
            questionary.Choice(
                title=f"[Download All] ({len(files)} files)",
                value="ALL",
            )
        ]
        for f in files:
            path = f.get("path") or f.get("Path") or "Unknown"
            length = int(f.get("length", f.get("Length", 0)))
            file_choices.append(
                questionary.Choice(
                    title=f"{path} ({format_bytes(length)})",
                    value=f,
                )
            )
        file_choices.append(questionary.Choice(title="[Cancel]", value=None))

        try:
            ans = questionary.select("Select files to download:", choices=file_choices).ask()
        except (KeyboardInterrupt, EOFError):
            return

        if not ans:
            return
        if ans == "ALL":
            to_download = files
        else:
            to_download = [ans]

    console = get_console()
    for f in to_download:
        f_id = int(f.get("id", f.get("Id", 0)))
        f_rel_path = f.get("path") or f.get("Path") or f"file_{f_id}.dat"
        f_len = int(f.get("length", f.get("Length", 0)))

        dest_file = target_path / f_rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        stream_url = ts_client.get_stream_url(chosen.infohash, file_id=f_id, filename=dest_file.name)

        progress = Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

        task_id = progress.add_task(f"downloading {dest_file.name}", total=f_len if f_len > 0 else None)
        try:
            with progress:
                with httpx.stream("GET", stream_url, timeout=30.0) as resp:
                    resp.raise_for_status()
                    with open(dest_file, "wb") as out_fp:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            out_fp.write(chunk)
                            progress.update(task_id, advance=len(chunk))
        except (KeyboardInterrupt, Exception) as err:
            status(f"download interrupted: {err}")
            break

    status(f"saved to {target_path}")
