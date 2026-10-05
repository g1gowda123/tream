import asyncio
from pathlib import Path
import sys
from typing import Any
import questionary
from tream.core.config import load_config
from tream.core.player import PlayerRunner, detect_player
from tream.core.search import search_all
from tream.core.series import (
    EpisodeInfo,
    VIDEO_EXTENSIONS,
    build_season_playlist,
    get_resume_position,
    group_and_sort_episodes,
    save_resume_position,
)
from tream.core.torrserver import (
    TorrServerClient,
    find_torrserver_binary,
    spawn_torrserver,
    stop_torrserver,
)
from tream.providers.base import SearchResult
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import get_logger, spinner, status
from tream.utils.subtitles import fetch_subtitles


def _pick_search_result(results: list[SearchResult]) -> SearchResult | None:
    choices: list[questionary.Choice] = []
    for r in results:
        label = f"[{r.provider}] {r.title} | {r.formatted_size} | S:{r.seeders} L:{r.leechers} [{r.quality}]"
        choices.append(questionary.Choice(title=label, value=r))
    choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        return questionary.select("Select result to stream:", choices=choices).ask()
    except (KeyboardInterrupt, EOFError):
        return None


def _find_primary_file(files: list[dict[str, Any]]) -> dict[str, Any]:
    """Find the largest video file or largest overall file."""
    video_files: list[dict[str, Any]] = []
    for f in files:
        p = f.get("path") or f.get("Path") or ""
        if Path(p).suffix.lower() in VIDEO_EXTENSIONS:
            video_files.append(f)

    candidate_pool = video_files if video_files else files
    if not candidate_pool:
        return {}

    return max(candidate_pool, key=lambda f: int(f.get("length", f.get("Length", 0))))


def watch_stream(
    query: str,
    is_series: bool = False,
    buffer_override: str | None = None,
    player_override: str | None = None,
) -> None:
    """Core flow: search, interactive picker, TorrServer buffer setup, and playback."""
    cfg = load_config()

    status("searching indexes...")
    results = asyncio.run(search_all(query, cfg))

    if not results:
        status("no results found")
        return

    status(f"found {len(results)} results")
    chosen = _pick_search_result(results)
    if not chosen:
        return

    _stream_torrent(
        chosen.magnet,
        chosen.title,
        chosen.infohash,
        is_series=is_series,
        buffer_override=buffer_override,
        player_override=player_override,
    )


def _stream_torrent(
    magnet: str,
    title: str,
    infohash: str,
    is_series: bool = False,
    buffer_override: str | None = None,
    player_override: str | None = None,
    resume_seconds: float = 0.0,
    forced_season: int | None = None,
    forced_episode: int | None = None,
) -> None:
    cfg = load_config()
    logger = get_logger()

    # Determine player and buffer
    try:
        player_binary = detect_player(player_override or cfg.get("general", {}).get("player"))
    except FileNotFoundError as err:
        status(str(err))
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
        try:
            spawn_torrserver(port=port, binary_path=torrserver_bin)
        except Exception as err:
            status(f"failed to start torrserver: {err}")
            return

    ts_client = TorrServerClient(port=port)
    ts_client.configure_settings(
        buffer_bytes=buf_bytes,
        preload_cache=cfg.get("playback", {}).get("preload_cache", 30),
        connections_limit=cfg.get("playback", {}).get("connections_limit", 800),
        enable_dht=cfg.get("playback", {}).get("enable_dht", True),
    )

    with spinner("fetching torrent metadata..."):
        try:
            ts_client.add_torrent(magnet, title)
            files = ts_client.wait_for_files(infohash, timeout=40.0)
        except TimeoutError:
            status("metadata timeout; torrent has no active seeders")
            return
        except Exception as err:
            status(f"failed to load torrent: {err}")
            return

    from tream.commands.history import add_history_entry

    if is_series or (cfg.get("series", {}).get("auto_group", True) and len(files) > 1):
        seasons = group_and_sort_episodes(files)
        if seasons:
            season_keys = sorted(seasons.keys())
            chosen_season = season_keys[0]

            if forced_season and forced_season in seasons:
                chosen_season = forced_season
            elif len(season_keys) > 1:
                season_choices = [
                    questionary.Choice(title=f"Season {s} ({len(seasons[s])} episodes)", value=s)
                    for s in season_keys
                ]
                season_choices.append(questionary.Choice(title="[Cancel]", value=None))
                try:
                    s_ans = questionary.select("Select season:", choices=season_choices).ask()
                except (KeyboardInterrupt, EOFError):
                    return
                if s_ans is None:
                    return
                chosen_season = s_ans

            episodes = seasons[chosen_season]

            # Check if there is existing resume state
            resume_state = get_resume_position(infohash)
            start_pos = resume_seconds

            selected_ep: EpisodeInfo | None = None
            if forced_episode:
                for ep in episodes:
                    if ep.episode == forced_episode:
                        selected_ep = ep
                        break

            if not selected_ep:
                ep_choices: list[questionary.Choice] = []
                for ep in episodes:
                    ep_choices.append(questionary.Choice(title=ep.display_name, value=ep))
                ep_choices.append(questionary.Choice(title="[Cancel]", value=None))

                try:
                    selected_ep = questionary.select(
                        f"Select episode (Season {chosen_season}):",
                        choices=ep_choices,
                    ).ask()
                except (KeyboardInterrupt, EOFError):
                    return

            if not selected_ep:
                return

            ep_index = episodes.index(selected_ep)
            if resume_state and resume_state.get("episode") == selected_ep.episode and start_pos == 0.0:
                start_pos = float(resume_state.get("timestamp", 0.0))

            playlist_path = build_season_playlist(
                episodes,
                start_index=ep_index,
                base_stream_url_getter=lambda fid, fn: ts_client.get_stream_url(infohash, file_id=fid, filename=fn),
            )

            sub_file = fetch_subtitles(selected_ep.title, config=cfg)

            status("buffering...")
            status("launching player...")

            runner = PlayerRunner(
                player_binary=player_binary,
                buffer_bytes=buf_bytes,
                title=f"{title} - {selected_ep.display_name}",
                resume_seconds=start_pos,
                subtitle_file=sub_file,
            )

            final_pos = runner.play(str(playlist_path), is_playlist=True)

            save_resume_position(
                infohash=infohash,
                season=selected_ep.season,
                episode=selected_ep.episode,
                file_id=selected_ep.file_id,
                timestamp=final_pos,
                title=title,
            )

            add_history_entry(
                title=title,
                magnet=magnet,
                infohash=infohash,
                position_seconds=final_pos,
                is_series=True,
                season=selected_ep.season,
                episode=selected_ep.episode,
            )

            status("cleaning up...")
            stop_torrserver()
            return

    # Single movie / standalone file workflow
    primary_file = _find_primary_file(files)
    file_id = int(primary_file.get("id", primary_file.get("Id", 0)))
    filename = Path(primary_file.get("path") or primary_file.get("Path") or "video.mkv").name

    stream_url = ts_client.get_stream_url(infohash, file_id=file_id, filename=filename)
    sub_file = fetch_subtitles(title, config=cfg)

    status("buffering...")
    status("launching player...")

    runner = PlayerRunner(
        player_binary=player_binary,
        buffer_bytes=buf_bytes,
        title=title,
        resume_seconds=resume_seconds,
        subtitle_file=sub_file,
    )

    final_pos = runner.play(stream_url, is_playlist=False)

    add_history_entry(
        title=title,
        magnet=magnet,
        infohash=infohash,
        position_seconds=final_pos,
        is_series=False,
    )

    status("cleaning up...")
    stop_torrserver()


def resume_from_history(
    entry: dict[str, Any],
    buffer_override: str | None = None,
    player_override: str | None = None,
) -> None:
    """Re-stream an item selected from watch history."""
    magnet = entry.get("magnet", "")
    infohash = entry.get("infohash", "")
    title = entry.get("title", "Unknown")
    pos = float(entry.get("position", 0.0))
    is_series = bool(entry.get("is_series", False))
    season = entry.get("season", 1)
    episode = entry.get("episode", 1)

    _stream_torrent(
        magnet=magnet,
        title=title,
        infohash=infohash,
        is_series=is_series,
        buffer_override=buffer_override,
        player_override=player_override,
        resume_seconds=pos,
        forced_season=season,
        forced_episode=episode,
    )
