import asyncio
from pathlib import Path
import sys
import threading
from typing import Any
import httpx
import questionary
from tream.core.config import load_config
from tream.core.player import PlayerRunner, detect_player
from tream.core.search import search_all
from tream.core.series import (
    EpisodeInfo,
    VIDEO_EXTENSIONS,
    build_season_playlist,
    find_episode,
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
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import spinner, status
from tream.utils.magnet import parse_magnet
from tream.utils.picker import interactive_pick_result, pick_by_index, print_results_table

# Post-playback actions
ACTION_RESUME = "resume"
ACTION_NEXT = "next"
ACTION_BACK = "back_results"
ACTION_NEW_SEARCH = "new_search"
ACTION_QUIT = "quit"
ACTION_DONE = "done"

# Show one flat episode list (instead of season -> episode prompts) when the
# torrent has this many episodes or fewer across all seasons.
FLAT_PICKER_LIMIT = 60


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


def _format_seconds(seconds: float) -> str:
    sec = int(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class WatchRuntime:
    """Player/TorrServer setup that can be reused across several playbacks in one session."""

    def __init__(self, cfg: dict[str, Any], buffer_override: str | None, player_override: str | None) -> None:
        self.cfg = cfg
        self.player_binary = ""
        self.buf_bytes = 0
        self.port = cfg.get("general", {}).get("torrserver_port", 8090)
        self.error = ""
        self.last_loaded_infohash: str = ""
        self._buffer_override = buffer_override
        self._player_override = player_override

    def start(self) -> bool:
        try:
            self.player_binary = detect_player(
                self._player_override or self.cfg.get("general", {}).get("player")
            )
        except FileNotFoundError as err:
            self.error = str(err)
            return False

        buf_str = self._buffer_override or self.cfg.get("playback", {}).get("buffer", "256M")
        try:
            self.buf_bytes = parse_buffer_size(buf_str)
        except ValueError as err:
            self.error = f"invalid buffer size: {err}"
            return False

        torrserver_bin = find_torrserver_binary(
            self.cfg.get("general", {}).get("torrserver_binary", "torrserver")
        )
        with spinner("starting torrserver..."):
            try:
                spawn_torrserver(port=self.port, binary_path=torrserver_bin)
            except Exception as err:
                self.error = f"failed to start torrserver: {err}"
                return False
        return True

    def client(self) -> TorrServerClient:
        client = TorrServerClient(port=self.port)
        client.configure_settings(
            buffer_bytes=self.buf_bytes,
            preload_cache=self.cfg.get("playback", {}).get("preload_cache", 10),
            connections_limit=self.cfg.get("playback", {}).get("connections_limit", 800),
            enable_dht=self.cfg.get("playback", {}).get("enable_dht", True),
        )
        return client

    def stop(self) -> None:
        if stop_torrserver():
            status("cleaning up...")


def watch_stream(
    query: str,
    is_series: bool = False,
    buffer_override: str | None = None,
    player_override: str | None = None,
    verbose: bool = False,
    pick_index: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    providers: list[str] | None = None,
    min_seeders: int | None = None,
    once: bool = False,
) -> int:
    """Core flow: search, pick, stream, then offer to keep going. Returns an exit code."""
    cfg = load_config()
    runtime = WatchRuntime(cfg, buffer_override, player_override)
    if not runtime.start():
        status(runtime.error)
        return 1

    try:
        current_query = query
        while True:
            status("searching indexes...")
            results = asyncio.run(
                search_all(current_query, cfg, providers_override=providers, min_seeders=min_seeders)
            )
            if not results:
                status("no results found")
                return 1

            action = _result_pick_loop(
                runtime,
                current_query,
                results,
                cfg,
                is_series=is_series,
                pick_index=pick_index,
                season=season,
                episode=episode,
                verbose=verbose,
                once=once,
            )
            # Only the first result selection uses --pick; anything after that is a picker
            pick_index = None

            if action == ACTION_NEW_SEARCH:
                new_query = _prompt_query_again(current_query)
                if not new_query:
                    return 0
                current_query = new_query
                continue
            return 0
    finally:
        runtime.stop()


def _result_pick_loop(
    runtime: WatchRuntime,
    query: str,
    results: list[Any],
    cfg: dict[str, Any],
    is_series: bool,
    pick_index: int | None,
    season: int | None,
    episode: int | None,
    verbose: bool,
    once: bool,
) -> str:
    """Pick a result and play it, looping back to the picker when requested."""
    while True:
        if pick_index is not None:
            try:
                chosen = pick_by_index(results, pick_index)
            except IndexError as err:
                status(f"{err}")
                status("run 'tream --list <query>' to see the ranked results")
                return ACTION_QUIT
            status(f"selected: {chosen.title} [{chosen.quality}] S:{chosen.seeders}")
        else:
            chosen = interactive_pick_result(query, results, page_size=10)
            if not chosen:
                return ACTION_QUIT

        action = _play_torrent_session(
            runtime,
            chosen.magnet,
            chosen.title,
            chosen.infohash,
            cfg,
            is_series=is_series,
            resume_seconds=0.0,
            forced_season=season,
            forced_episode=episode,
            verbose=verbose,
            once=once,
            allow_result_loop=True,
        )
        if action == ACTION_BACK:
            continue
        return action


def stream_magnet(
    magnet_or_hash: str,
    title: str = "",
    is_series: bool = False,
    buffer_override: str | None = None,
    player_override: str | None = None,
    resume_seconds: float = 0.0,
    forced_season: int | None = None,
    forced_episode: int | None = None,
    verbose: bool = False,
    once: bool = False,
) -> int:
    """Stream a magnet URI, raw infohash, or .torrent URL directly without searching."""
    try:
        info = parse_magnet(magnet_or_hash)
    except ValueError as err:
        status(f"invalid magnet/infohash: {err}")
        return 1

    cfg = load_config()
    runtime = WatchRuntime(cfg, buffer_override, player_override)
    if not runtime.start():
        status(runtime.error)
        return 1

    try:
        action = _play_torrent_session(
            runtime,
            info.raw_uri,
            title.strip() or info.name or info.infohash,
            info.infohash,
            cfg,
            is_series=is_series,
            resume_seconds=resume_seconds,
            forced_season=forced_season,
            forced_episode=forced_episode,
            verbose=verbose,
            once=once,
            allow_result_loop=False,
        )
    finally:
        runtime.stop()

    if action == ACTION_NEW_SEARCH:
        new_query = _prompt_query_again("")
        if new_query:
            return watch_stream(
                new_query,
                is_series=is_series,
                buffer_override=buffer_override,
                player_override=player_override,
                verbose=verbose,
                once=once,
            )
    return 0


def _play_torrent_session(
    runtime: WatchRuntime,
    magnet: str,
    title: str,
    infohash: str,
    cfg: dict[str, Any],
    is_series: bool,
    resume_seconds: float,
    forced_season: int | None,
    forced_episode: int | None,
    verbose: bool,
    once: bool,
    allow_result_loop: bool,
) -> str:
    """Load one torrent and play it, returning the post-playback action."""
    client = runtime.client()

    # Drop the previously streamed torrent when switching to a different one
    if runtime.last_loaded_infohash and runtime.last_loaded_infohash != infohash.lower():
        client.drop_torrent(runtime.last_loaded_infohash)
        runtime.last_loaded_infohash = ""

    with spinner("fetching torrent metadata..."):
        try:
            client.add_torrent(magnet, title)
            files = client.wait_for_files(infohash, timeout=40.0)
        except TimeoutError:
            status("metadata timeout; torrent has no active seeders")
            return ACTION_DONE
        except Exception as err:
            status(f"failed to load torrent: {err}")
            return ACTION_DONE
    runtime.last_loaded_infohash = infohash.lower()

    if is_series or (cfg.get("series", {}).get("auto_group", True) and len(files) > 1):
        seasons = group_and_sort_episodes(files)
        if seasons:
            return _play_series_session(
                runtime,
                client,
                seasons,
                magnet,
                title,
                infohash,
                cfg,
                resume_seconds,
                forced_season,
                forced_episode,
                verbose,
                once,
                allow_result_loop,
            )

    return _play_movie_session(
        runtime,
        client,
        files,
        magnet,
        title,
        infohash,
        cfg,
        resume_seconds,
        verbose,
        once,
        allow_result_loop,
    )


def _play_movie_session(
    runtime: WatchRuntime,
    client: TorrServerClient,
    files: list[dict[str, Any]],
    magnet: str,
    title: str,
    infohash: str,
    cfg: dict[str, Any],
    resume_seconds: float,
    verbose: bool,
    once: bool,
    allow_result_loop: bool,
) -> str:
    from tream.commands.history import add_history_entry

    primary_file = _find_primary_file(files)
    file_id = int(primary_file.get("id", primary_file.get("Id", 0)))
    filename = Path(primary_file.get("path") or primary_file.get("Path") or "video.mkv").name
    start_pos = resume_seconds

    while True:
        stream_url = client.get_stream_url(infohash, file_id=file_id, filename=filename)
        sub_file = _fetch_subtitles_safe(title, cfg)

        status("buffering...")
        runner = PlayerRunner(
            player_binary=runtime.player_binary,
            buffer_bytes=runtime.buf_bytes,
            title=title,
            resume_seconds=start_pos,
            subtitle_file=sub_file,
            verbose=verbose,
        )

        final_pos = runner.play(stream_url, is_playlist=False)

        add_history_entry(
            title=title,
            magnet=magnet,
            infohash=infohash,
            position_seconds=final_pos,
            is_series=False,
        )

        if once or not sys.stdin.isatty():
            return ACTION_DONE

        action, _ = _post_play_menu(
            title, final_pos=final_pos, next_ep=None, allow_result_loop=allow_result_loop
        )
        if action == ACTION_RESUME:
            start_pos = final_pos if final_pos > 5.0 else 0.0
            continue
        return action


def _play_series_session(
    runtime: WatchRuntime,
    client: TorrServerClient,
    seasons: dict[int, list[EpisodeInfo]],
    magnet: str,
    title: str,
    infohash: str,
    cfg: dict[str, Any],
    resume_seconds: float,
    forced_season: int | None,
    forced_episode: int | None,
    verbose: bool,
    once: bool,
    allow_result_loop: bool,
) -> str:
    from tream.commands.history import add_history_entry

    resume_state = get_resume_position(infohash)

    # Start fetching the resume target (or first file) while the user is picking
    _start_prewarm(client, infohash, seasons, cfg, resume_state)

    selected_ep = _select_episode(seasons, forced_season, forced_episode, infohash)
    if selected_ep is None:
        return ACTION_DONE

    start_pos = resume_seconds
    if resume_state and resume_state.get("episode") == selected_ep.episode and start_pos == 0.0:
        start_pos = float(resume_state.get("timestamp", 0.0))

    while True:
        episodes = seasons[selected_ep.season]
        ep_index = episodes.index(selected_ep)

        playlist_path = build_season_playlist(
            episodes,
            start_index=ep_index,
            base_stream_url_getter=lambda fid, fn: client.get_stream_url(infohash, file_id=fid, filename=fn),
        )

        sub_file = _fetch_subtitles_safe(selected_ep.title, cfg)

        status("buffering...")
        runner = PlayerRunner(
            player_binary=runtime.player_binary,
            buffer_bytes=runtime.buf_bytes,
            title=f"{title} - {selected_ep.display_name}",
            resume_seconds=start_pos,
            subtitle_file=sub_file,
            verbose=verbose,
        )

        try:
            final_pos = runner.play(str(playlist_path), is_playlist=True)
        finally:
            try:
                playlist_path.unlink(missing_ok=True)
            except OSError:
                pass

        # MPV may have auto-advanced through the playlist; remember the episode
        # that actually ended so resume/history point at the right one.
        if runner.last_playlist_pos is not None and runner.last_playlist_pos > 0:
            ended_index = min(ep_index + runner.last_playlist_pos, len(episodes) - 1)
            selected_ep = episodes[ended_index]
            ep_index = ended_index

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

        if once or not sys.stdin.isatty():
            return ACTION_DONE

        next_ep = episodes[ep_index + 1] if ep_index + 1 < len(episodes) else None
        action, target_ep = _post_play_menu(
            title,
            final_pos=final_pos,
            next_ep=next_ep,
            allow_result_loop=allow_result_loop,
            current_ep=selected_ep,
        )

        if action == ACTION_RESUME:
            start_pos = final_pos if final_pos > 5.0 else 0.0
            continue
        if action == ACTION_NEXT and target_ep is not None:
            selected_ep = target_ep
            start_pos = 0.0
            continue
        return action


def _select_episode(
    seasons: dict[int, list[EpisodeInfo]],
    forced_season: int | None,
    forced_episode: int | None,
    infohash: str,
) -> EpisodeInfo | None:
    """Resolve the episode to play, prompting only when necessary."""
    season_keys = sorted(seasons.keys())
    resume_state = get_resume_position(infohash)

    if forced_episode is not None and forced_season is None:
        matched = find_episode(seasons, None, forced_episode)
        if matched is not None:
            return matched
        status(f"episode {forced_episode} not found in this torrent; choose one manually")

    if forced_season is not None:
        if forced_season not in seasons:
            available = ", ".join(str(s) for s in season_keys)
            status(f"season {forced_season} not found (available: {available})")
            return None
        if forced_episode is not None:
            matched = find_episode(seasons, forced_season, forced_episode)
            if matched is not None:
                return matched
            status(f"episode {forced_episode} not found in season {forced_season}; choose one manually")
        episodes = seasons[forced_season]
        if len(episodes) == 1:
            return episodes[0]
        if not sys.stdin.isatty():
            status("no interactive terminal; specify --episode N to choose non-interactively")
            return None
        return _prompt_episode(episodes, forced_season, resume_state)

    if not sys.stdin.isatty():
        status("no interactive terminal; specify --season N and --episode N to choose non-interactively")
        return None

    total_episodes = sum(len(v) for v in seasons.values())
    if len(season_keys) > 1 and total_episodes <= FLAT_PICKER_LIMIT:
        return _prompt_episode_flat(seasons, resume_state)

    chosen_season = season_keys[0]
    if len(season_keys) > 1:
        season_choices = [
            questionary.Choice(title=f"Season {s} ({len(seasons[s])} episodes)", value=s)
            for s in season_keys
        ]
        season_choices.append(questionary.Choice(title="[Cancel]", value=None))
        try:
            s_ans = questionary.select("Select season:", choices=season_choices).ask()
        except (KeyboardInterrupt, EOFError):
            return None
        if s_ans is None:
            return None
        chosen_season = s_ans

    episodes = seasons[chosen_season]
    if len(episodes) == 1:
        return episodes[0]
    return _prompt_episode(episodes, chosen_season, resume_state)


def _episode_label(ep: EpisodeInfo, resume_state: dict[str, Any] | None) -> str:
    label = ep.display_name
    if (
        resume_state
        and resume_state.get("episode") == ep.episode
        and resume_state.get("season") == ep.season
    ):
        label += f"  (resume {_format_seconds(float(resume_state.get('timestamp', 0.0)))})"
    return label


def _prompt_episode(
    episodes: list[EpisodeInfo],
    chosen_season: int,
    resume_state: dict[str, Any] | None,
) -> EpisodeInfo | None:
    ep_choices: list[Any] = [
        questionary.Choice(title=_episode_label(ep, resume_state), value=ep) for ep in episodes
    ]
    ep_choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        selected_ep = questionary.select(
            f"Select episode (Season {chosen_season}):",
            choices=ep_choices,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return None
    return selected_ep


def _prompt_episode_flat(
    seasons: dict[int, list[EpisodeInfo]],
    resume_state: dict[str, Any] | None,
) -> EpisodeInfo | None:
    """One episode list spanning all seasons, with separators per season."""
    choices: list[Any] = []
    for season_num in sorted(seasons):
        choices.append(questionary.Separator(f"── Season {season_num} ──"))
        for ep in seasons[season_num]:
            choices.append(questionary.Choice(title=_episode_label(ep, resume_state), value=ep))
    choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        selected_ep = questionary.select("Select episode:", choices=choices).ask()
    except (KeyboardInterrupt, EOFError):
        return None
    return selected_ep


def _post_play_menu(
    title: str,
    final_pos: float,
    next_ep: EpisodeInfo | None,
    allow_result_loop: bool,
    current_ep: EpisodeInfo | None = None,
) -> tuple[str, EpisodeInfo | None]:
    """Ask what to do after playback ends. Returns (action, target_episode)."""
    if final_pos > 5.0:
        what = current_ep.display_name if current_ep else title
        resume_label = f"Resume {what} from {_format_seconds(final_pos)}"
    else:
        resume_label = "Play again from start"

    choices: list[Any] = [questionary.Choice(title=resume_label, value=(ACTION_RESUME, None))]
    if next_ep is not None:
        choices.append(
            questionary.Choice(title=f"Next episode: {next_ep.display_name}", value=(ACTION_NEXT, next_ep))
        )
    if allow_result_loop:
        choices.append(questionary.Choice(title="Pick another result", value=(ACTION_BACK, None)))
    choices.append(questionary.Choice(title="New search", value=(ACTION_NEW_SEARCH, None)))
    choices.append(questionary.Choice(title="Quit tream", value=(ACTION_QUIT, None)))

    try:
        selection = questionary.select("Playback finished — what next?", choices=choices).ask()
    except (KeyboardInterrupt, EOFError):
        return ACTION_QUIT, None

    if not selection:
        return ACTION_QUIT, None
    return selection


def _prompt_query_again(previous: str) -> str:
    try:
        answer = questionary.text("Search:", default=previous).ask()
    except (KeyboardInterrupt, EOFError):
        return ""
    return (answer or "").strip()


def _pick_prewarm_episode(
    seasons: dict[int, list[EpisodeInfo]],
    resume_state: dict[str, Any] | None,
) -> EpisodeInfo | None:
    """Choose the episode worth preloading: the resume target, else the first."""
    if resume_state:
        try:
            matched = find_episode(
                seasons,
                int(resume_state.get("season") or 0) or None,
                int(resume_state.get("episode") or 0),
            )
        except (TypeError, ValueError):
            matched = None
        if matched is not None:
            return matched

    if not seasons:
        return None
    first_season = min(seasons)
    episodes = seasons[first_season]
    return episodes[0] if episodes else None


def _start_prewarm(
    client: TorrServerClient,
    infohash: str,
    seasons: dict[int, list[EpisodeInfo]],
    cfg: dict[str, Any],
    resume_state: dict[str, Any] | None = None,
) -> None:
    """Begin fetching the resume target (or first episode) while the user picks."""
    if not cfg.get("playback", {}).get("prewarm", True):
        return

    target = _pick_prewarm_episode(seasons, resume_state)
    if target is None:
        return

    url = client.get_stream_url(infohash, file_id=target.file_id, filename=Path(target.path).name)
    threading.Thread(target=_prewarm_stream, args=(url,), daemon=True).start()


def _prewarm_stream(url: str) -> None:
    """Fetch a small chunk of the stream to nudge TorrServer into downloading pieces."""
    try:
        headers = {"Range": "bytes=0-262143"}
        timeout = httpx.Timeout(20.0, connect=5.0, read=20.0)
        with httpx.stream("GET", url, headers=headers, timeout=timeout) as resp:
            resp.raise_for_status()
            for _ in resp.iter_bytes(chunk_size=65536):
                break
    except Exception:
        pass


def _fetch_subtitles_safe(title: str, cfg: dict[str, Any]) -> Path | None:
    """Fetch subtitles without ever failing the playback flow."""
    try:
        from tream.utils.subtitles import fetch_subtitles

        return fetch_subtitles(title, config=cfg)
    except Exception:
        return None


def resume_from_history(
    entry: dict[str, Any],
    buffer_override: str | None = None,
    player_override: str | None = None,
    verbose: bool = False,
    once: bool = False,
) -> int:
    """Re-stream an item selected from watch history."""
    magnet = entry.get("magnet", "")
    infohash = entry.get("infohash", "")
    title = entry.get("title", "Unknown")
    pos = float(entry.get("position", 0.0))
    is_series = bool(entry.get("is_series", False))
    season = entry.get("season", 1)
    episode = entry.get("episode", 1)

    cfg = load_config()
    runtime = WatchRuntime(cfg, buffer_override, player_override)
    if not runtime.start():
        status(runtime.error)
        return 1

    try:
        return_value = _play_torrent_session(
            runtime,
            magnet,
            title,
            infohash,
            cfg,
            is_series=is_series,
            resume_seconds=pos,
            forced_season=season,
            forced_episode=episode,
            verbose=verbose,
            once=once,
            allow_result_loop=False,
        )
    finally:
        runtime.stop()

    if return_value == ACTION_NEW_SEARCH:
        new_query = _prompt_query_again("")
        if new_query:
            return watch_stream(
                new_query,
                buffer_override=buffer_override,
                player_override=player_override,
                verbose=verbose,
                once=once,
            )
    return 0


def list_results(
    query: str,
    providers: list[str] | None = None,
    min_seeders: int | None = None,
) -> int:
    """Print ranked search results as a table without any prompts."""
    cfg = load_config()
    status("searching indexes...")
    results = asyncio.run(
        search_all(query, cfg, providers_override=providers, min_seeders=min_seeders)
    )
    print_results_table(query, results)
    return 0 if results else 1
