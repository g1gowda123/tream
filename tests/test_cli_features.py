import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tream.commands.download import (
    _partial_path,
    _sort_files_for_selection,
    resolve_download_target,
)
from tream.main import app
from tream.providers.base import SearchResult
from tream.utils.magnet import looks_like_torrent_ref
from tream.utils.picker import pick_by_index, sort_results

HASH_A = "e36780c888d30e52516fa8a4f02fa5ac143cebb2"
HASH_B = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _result(title: str, seeders: int, size: int = 1024, quality: str = "1080P") -> SearchResult:
    return SearchResult(
        title=title,
        magnet=f"magnet:?xt=urn:btih:{HASH_A}",
        infohash=HASH_A,
        size=size,
        seeders=seeders,
        leechers=1,
        provider="test",
        quality=quality,
        category="Movies",
    )


# ---------------------------------------------------------------------------
# Download target resolution
# ---------------------------------------------------------------------------

def test_resolve_download_target_multiword_query_keeps_words():
    query, folder = resolve_download_target(["dune", "part", "two"])
    assert query == "dune part two"
    assert folder == Path.cwd()


def test_resolve_download_target_path_like_last_arg():
    query, folder = resolve_download_target(["ubuntu", "./isos"])
    assert query == "ubuntu"
    assert folder == Path("./isos").expanduser()


def test_resolve_download_target_existing_directory(tmp_path):
    query, folder = resolve_download_target(["the bear", str(tmp_path)])
    assert query == "the bear"
    assert folder == tmp_path


def test_resolve_download_target_explicit_output():
    query, folder = resolve_download_target(["dune"], explicit="/tmp/out")
    assert query == "dune"
    assert folder == Path("/tmp/out")


def test_partial_path_has_part_suffix():
    assert _partial_path(Path("/a/b/movie.mkv")).name == "movie.mkv.part"


def test_sort_files_for_selection_prefers_video_and_size():
    files = [
        {"id": 1, "path": "readme.nfo", "length": 10},
        {"id": 2, "path": "b.mkv", "length": 100},
        {"id": 3, "path": "a.mkv", "length": 900},
    ]
    ordered = _sort_files_for_selection(files)
    assert [f["id"] for f in ordered] == [3, 2, 1]


# ---------------------------------------------------------------------------
# Picker helpers
# ---------------------------------------------------------------------------

def test_pick_by_index_is_one_based():
    results = [_result("first", 5), _result("second", 1)]
    assert pick_by_index(results, 1).title == "first"
    assert pick_by_index(results, 2).title == "second"


def test_pick_by_index_out_of_range():
    results = [_result("only", 5)]
    with pytest.raises(IndexError):
        pick_by_index(results, 0)
    with pytest.raises(IndexError):
        pick_by_index(results, 2)


def test_sort_results_modes():
    low = _result("low", seeders=1, size=2048, quality="720P")
    high = _result("high", seeders=50, size=1024, quality="2160P")
    original = [low, high]

    assert sort_results([low, high], "seeders")[0] is high
    assert sort_results([low, high], "size")[0] is low
    assert sort_results([low, high], "quality")[0] is high
    assert sort_results([low, high], "title")[0] is high  # "high" < "low" alphabetically
    assert original == [low, high]  # input order must not be mutated


def test_quality_rank_orders_resolutions():
    from tream.utils.picker import quality_rank

    assert quality_rank("2160P") > quality_rank("1080P") > quality_rank("720P") > quality_rank("480P")
    assert quality_rank("4K") == 2160
    assert quality_rank("Unknown") == 0
    assert quality_rank("") == 0


# ---------------------------------------------------------------------------
# Magnet detection
# ---------------------------------------------------------------------------

def test_looks_like_torrent_ref():
    assert looks_like_torrent_ref(f"magnet:?xt=urn:btih:{HASH_A}")
    assert looks_like_torrent_ref(HASH_A)
    assert looks_like_torrent_ref("M2HLBEGSNDBTZ7BHP3TVVWZO2ZZZZZZZ")
    assert not looks_like_torrent_ref("blade runner")
    assert not looks_like_torrent_ref("")


# ---------------------------------------------------------------------------
# Episode lookup
# ---------------------------------------------------------------------------

def test_find_episode_across_seasons():
    from tream.core.series import EpisodeInfo, find_episode

    seasons = {
        1: [EpisodeInfo(file_id=1, path="a.mkv", season=1, episode=1, size=0, title="a")],
        2: [EpisodeInfo(file_id=2, path="b.mkv", season=2, episode=5, size=0, title="b")],
    }
    assert find_episode(seasons, 2, 5).file_id == 2
    assert find_episode(seasons, None, 5).season == 2
    assert find_episode(seasons, 1, 5) is None
    assert find_episode(seasons, None, 99) is None


# ---------------------------------------------------------------------------
# Search filters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_all_min_seeders(monkeypatch):
    from tream.core import search as search_mod

    class FakeProvider:
        name = "fake"

        async def search(self, query):
            return [_result("weak", seeders=2), _result("strong", seeders=30)]

    monkeypatch.setattr(search_mod, "get_enabled_providers", lambda cfg=None, providers_override=None: [FakeProvider()])
    results = await search_mod.search_all("x", {"search": {"fuzzy": False, "min_seeders": 10}})
    assert [r.title for r in results] == ["strong"]


def test_get_enabled_providers_override():
    from tream.core.search import get_enabled_providers

    providers = get_enabled_providers({"search": {"providers": []}}, providers_override=["yts"])
    assert [p.name for p in providers] == ["yts"]


# ---------------------------------------------------------------------------
# Resumable downloads
# ---------------------------------------------------------------------------

class _FakeStreamResponse:
    def __init__(self, chunks, status_code=200, headers=None):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, chunk_size=65536):
        yield from self._chunks


class _FakeClient:
    def get_stream_url(self, infohash, file_id=None, filename=""):
        return "http://127.0.0.1:8090/stream/fake"


def _fake_stream_factory(responses):
    """Return an httpx.stream replacement that pops responses in order."""
    calls = []

    def _stream(method, url, **kwargs):
        calls.append({"url": url, "headers": kwargs.get("headers", {})})
        return responses.pop(0)

    _stream.calls = calls
    return _stream


def test_download_file_fresh(tmp_path, monkeypatch):
    from tream.commands import download as dl

    content = b"hello world"
    monkeypatch.setattr(
        dl.httpx, "stream", _fake_stream_factory([_FakeStreamResponse([content])])
    )
    status, written = dl._download_file(
        _FakeClient(), HASH_A, {"id": 1, "path": "movie.mkv", "length": len(content)}, tmp_path
    )
    assert status == "downloaded"
    assert written == len(content)
    assert (tmp_path / "movie.mkv").read_bytes() == content
    assert not (tmp_path / "movie.mkv.part").exists()


def test_download_file_resumes_partial(tmp_path, monkeypatch):
    from tream.commands import download as dl

    dest = tmp_path / "movie.mkv"
    part = _partial_path(dest)
    part.write_bytes(b"hello ")
    stream = _fake_stream_factory(
        [_FakeStreamResponse([b"world"], status_code=206, headers={"content-length": "5"})]
    )
    monkeypatch.setattr(dl.httpx, "stream", stream)
    status, written = dl._download_file(
        _FakeClient(), HASH_A, {"id": 1, "path": "movie.mkv", "length": 11}, tmp_path
    )
    assert status == "resumed"
    assert written == 5
    assert dest.read_bytes() == b"hello world"
    assert stream.calls[0]["headers"]["Range"] == "bytes=6-"


def test_download_file_restarts_when_range_ignored(tmp_path, monkeypatch):
    from tream.commands import download as dl

    dest = tmp_path / "movie.mkv"
    _partial_path(dest).write_bytes(b"stale")
    full_content = b"fresh-complete!"
    monkeypatch.setattr(
        dl.httpx,
        "stream",
        _fake_stream_factory([_FakeStreamResponse([full_content], status_code=200)]),
    )
    status, written = dl._download_file(
        _FakeClient(), HASH_A, {"id": 1, "path": "movie.mkv", "length": len(full_content)}, tmp_path
    )
    assert status == "downloaded"
    assert written == len(full_content)
    assert dest.read_bytes() == full_content


def test_download_file_skips_complete(tmp_path, monkeypatch):
    from tream.commands import download as dl

    dest = tmp_path / "movie.mkv"
    dest.write_bytes(b"complete")

    def _fail_stream(*args, **kwargs):
        raise AssertionError("should not stream when file is complete")

    monkeypatch.setattr(dl.httpx, "stream", _fail_stream)
    status, written = dl._download_file(
        _FakeClient(), HASH_A, {"id": 1, "path": "movie.mkv", "length": 8}, tmp_path
    )
    assert status == "skipped"
    assert written == 0


def test_download_file_interrupted_keeps_part(tmp_path, monkeypatch):
    from tream.commands import download as dl

    def _explode(*args, **kwargs):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(dl.httpx, "stream", _explode)
    status, _ = dl._download_file(
        _FakeClient(), HASH_A, {"id": 1, "path": "movie.mkv", "length": 100}, tmp_path
    )
    assert status == "failed"
    assert not (tmp_path / "movie.mkv").exists()


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

def test_cli_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "tream" in result.output


def test_cli_help_routing():
    root = CliRunner().invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "--download" in root.output

    config_help = CliRunner().invoke(app, ["config", "--help"])
    assert config_help.exit_code == 0
    assert "Manage tream configuration" in config_help.output

    get_help = CliRunner().invoke(app, ["config", "get", "--help"])
    assert get_help.exit_code == 0
    assert "Read a configuration value" in get_help.output


def test_cli_rejects_invalid_pick():
    result = CliRunner().invoke(app, ["blade runner", "--pick", "0"])
    assert result.exit_code == 1


def test_cli_rejects_unknown_provider():
    result = CliRunner().invoke(app, ["blade runner", "-P", "notarealprovider"])
    assert result.exit_code == 1
    assert "notarealprovider" in result.output


def test_cli_clear_without_history_flag():
    result = CliRunner().invoke(app, ["--clear"])
    assert result.exit_code == 1


def test_cli_history_clear(monkeypatch, tmp_path):
    from tream.commands import history as history_mod

    history_file = tmp_path / "history.json"
    history_file.write_text("[]")
    monkeypatch.setattr(history_mod, "get_history_path", lambda: history_file)
    result = CliRunner().invoke(app, ["-h", "--clear"])
    assert result.exit_code == 0
    assert not history_file.exists()


def test_cli_output_requires_download():
    result = CliRunner().invoke(app, ["movie", "-o", "/tmp/x"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Config CLI
# ---------------------------------------------------------------------------

def _run_config(args, monkeypatch, config):
    from tream.commands import config_cmd

    saved: dict = {}
    monkeypatch.setattr(config_cmd, "load_config", lambda: json.loads(json.dumps(config)))
    monkeypatch.setattr(config_cmd, "save_config", lambda data: saved.update(data))
    result = CliRunner().invoke(config_cmd.config_app, args)
    return result, saved


def test_config_set_rejects_invalid_buffer(monkeypatch):
    result, saved = _run_config(
        ["set", "playback.buffer", "nonsense"], monkeypatch, {"playback": {"buffer": "256M"}}
    )
    assert result.exit_code == 1
    assert saved == {}


def test_config_set_persists_valid_value(monkeypatch):
    result, saved = _run_config(
        ["set", "playback.buffer", "512M"], monkeypatch, {"playback": {"buffer": "256M"}}
    )
    assert result.exit_code == 0
    assert saved["playback"]["buffer"] == "512M"


def test_config_set_rejects_unknown_provider(monkeypatch):
    result, saved = _run_config(
        ["set", "search.providers", "[yts, bogus]"], monkeypatch, {"search": {"providers": ["yts"]}}
    )
    assert result.exit_code == 1
    assert saved == {}


def test_config_get_prints_value(monkeypatch):
    result, _ = _run_config(["get", "general.player"], monkeypatch, {"general": {"player": "mpv"}})
    assert result.exit_code == 0
    assert "mpv" in result.output


def test_config_validate_flags_bad_provider(monkeypatch, tmp_path):
    from tream.commands import config_cmd

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[search]\nproviders = ["yts", "bogus"]\n')
    monkeypatch.setattr(config_cmd, "get_config_path", lambda: cfg_file)
    result = CliRunner().invoke(config_cmd.config_app, ["validate"])
    assert result.exit_code == 1
    assert "bogus" in result.output


def test_config_set_rejects_non_bool_prewarm(monkeypatch):
    result, saved = _run_config(
        ["set", "playback.prewarm", "nonsense"], monkeypatch, {"playback": {}}
    )
    assert result.exit_code == 1
    assert saved == {}


# ---------------------------------------------------------------------------
# Playback session loop (post-playback menu)
# ---------------------------------------------------------------------------

class _FakeRuntime:
    def __init__(self, *args, **kwargs):
        self.player_binary = "mpv"
        self.buf_bytes = 1024
        self.port = 8090
        self.error = ""
        self.last_loaded_infohash = ""

    def start(self):
        return True

    def client(self):
        return _FakeTSClient()

    def stop(self):
        return None


class _FakeTSClient:
    def __init__(self):
        self.added = []
        self.dropped = []
        self.files = [{"id": 1, "path": "movie.mkv", "length": 100}]

    def configure_settings(self, **kwargs):
        return True

    def add_torrent(self, link, title=""):
        self.added.append(link)
        return {}

    def wait_for_files(self, infohash, timeout=35.0):
        return self.files

    def get_stream_url(self, infohash, file_id=None, filename=""):
        return "http://127.0.0.1:8090/stream/fake"

    def drop_torrent(self, infohash):
        self.dropped.append(infohash)


def _base_cfg():
    return {"general": {}, "playback": {}, "series": {}, "subtitles": {}}


def test_watch_stream_reuses_results_after_back(monkeypatch):
    from tream.commands import watch as w

    searches: list[str] = []

    async def fake_search(query, cfg, providers_override=None, min_seeders=None):
        searches.append(query)
        return [_result("a", 5), _result("b", 3)]

    monkeypatch.setattr(w, "WatchRuntime", _FakeRuntime)
    monkeypatch.setattr(w, "load_config", _base_cfg)
    monkeypatch.setattr(w, "search_all", fake_search)
    monkeypatch.setattr(w, "interactive_pick_result", lambda q, r, page_size=10: r[0])
    actions = iter([w.ACTION_BACK, w.ACTION_QUIT])
    monkeypatch.setattr(w, "_play_torrent_session", lambda *a, **k: next(actions))

    rc = w.watch_stream("blade runner")
    assert rc == 0
    assert searches == ["blade runner"]  # "back to results" must not re-search


def test_watch_stream_new_search_prompts_and_researches(monkeypatch):
    from tream.commands import watch as w

    searches: list[str] = []

    async def fake_search(query, cfg, providers_override=None, min_seeders=None):
        searches.append(query)
        return [_result("a", 5)]

    monkeypatch.setattr(w, "WatchRuntime", _FakeRuntime)
    monkeypatch.setattr(w, "load_config", _base_cfg)
    monkeypatch.setattr(w, "search_all", fake_search)
    monkeypatch.setattr(w, "interactive_pick_result", lambda q, r, page_size=10: r[0])
    monkeypatch.setattr(w, "_prompt_query_again", lambda prev: "dune")
    actions = iter([w.ACTION_NEW_SEARCH, w.ACTION_QUIT])
    monkeypatch.setattr(w, "_play_torrent_session", lambda *a, **k: next(actions))

    rc = w.watch_stream("first")
    assert rc == 0
    assert searches == ["first", "dune"]


def test_play_torrent_session_drops_previous_torrent(monkeypatch):
    from tream.commands import history as history_mod
    from tream.commands import watch as w

    class _FakeRunner:
        def __init__(self, **kwargs):
            pass

        def play(self, stream_url, is_playlist=False):
            return 0.0

    fake_client = _FakeTSClient()

    class _Runtime(_FakeRuntime):
        def client(self):
            return fake_client

    monkeypatch.setattr(w, "PlayerRunner", _FakeRunner)
    monkeypatch.setattr(w, "_fetch_subtitles_safe", lambda *a, **k: None)
    monkeypatch.setattr(history_mod, "add_history_entry", lambda **k: None)

    runtime = _Runtime()
    runtime.last_loaded_infohash = HASH_B
    action = w._play_torrent_session(
        runtime,
        f"magnet:?xt=urn:btih:{HASH_A}",
        "Movie",
        HASH_A,
        _base_cfg(),
        is_series=False,
        resume_seconds=0.0,
        forced_season=None,
        forced_episode=None,
        verbose=False,
        once=True,
        allow_result_loop=True,
    )
    assert action == w.ACTION_DONE
    assert fake_client.dropped == [HASH_B]
    assert runtime.last_loaded_infohash == HASH_A


def test_play_series_session_next_episode(monkeypatch, tmp_path):
    import sys as _sys

    from tream.commands import history as history_mod
    from tream.commands import watch as w
    from tream.core.series import EpisodeInfo

    class _TTY:
        def isatty(self):
            return True

    ep1 = EpisodeInfo(file_id=1, path="S01E01.mkv", season=1, episode=1, size=0, title="e1")
    ep2 = EpisodeInfo(file_id=2, path="S01E02.mkv", season=1, episode=2, size=0, title="e2")
    seasons = {1: [ep1, ep2]}

    played: list[str] = []
    builds: list[int] = []
    saved: list[dict] = []

    class _FakeRunner:
        def __init__(self, **kwargs):
            self.last_playlist_pos = None

        def play(self, stream_url, is_playlist=False):
            played.append(str(stream_url))
            return 100.0

    def fake_build(episodes, start_index, base_stream_url_getter):
        builds.append(start_index)
        path = tmp_path / f"playlist_{start_index}.m3u"
        path.write_text("#EXTM3U\n")
        return path

    menus = iter([(w.ACTION_NEXT, ep2), (w.ACTION_QUIT, None)])

    monkeypatch.setattr(_sys, "stdin", _TTY())
    monkeypatch.setattr(w, "PlayerRunner", _FakeRunner)
    monkeypatch.setattr(w, "build_season_playlist", fake_build)
    monkeypatch.setattr(w, "_fetch_subtitles_safe", lambda *a, **k: None)
    monkeypatch.setattr(w, "_start_prewarm", lambda *a, **k: None)
    monkeypatch.setattr(w, "_select_episode", lambda *a, **k: ep1)
    monkeypatch.setattr(w, "_post_play_menu", lambda *a, **k: next(menus))
    monkeypatch.setattr(w, "get_resume_position", lambda ih: None)
    monkeypatch.setattr(w, "save_resume_position", lambda **kw: saved.append(kw))
    monkeypatch.setattr(history_mod, "add_history_entry", lambda **k: None)

    action = w._play_series_session(
        _FakeRuntime(),
        _FakeTSClient(),
        seasons,
        "magnet:?xt=urn:btih:" + HASH_A,
        "Show",
        HASH_A,
        _base_cfg(),
        0.0,
        1,
        1,
        False,
        False,
        True,
    )
    assert action == w.ACTION_QUIT
    assert builds == [0, 1]  # second playback starts at the next episode
    assert [Path(p).name for p in played] == ["playlist_0.m3u", "playlist_1.m3u"]
    assert [(s["season"], s["episode"]) for s in saved] == [(1, 1), (1, 2)]
    # Playlists must be cleaned up
    assert not list(tmp_path.glob("playlist_*.m3u"))


def test_play_series_session_tracks_auto_advanced_episode(monkeypatch, tmp_path):
    from tream.commands import history as history_mod
    from tream.commands import watch as w
    from tream.core.series import EpisodeInfo

    class _TTY:
        def isatty(self):
            return True

    eps = [
        EpisodeInfo(file_id=i, path=f"S01E0{i}.mkv", season=1, episode=i, size=0, title=f"e{i}")
        for i in (1, 2, 3)
    ]
    seasons = {1: eps}
    saved: list[dict] = []
    menu_calls: list[dict] = []

    class _FakeRunner:
        def __init__(self, **kwargs):
            # MPV auto-advanced one episode from the selected one
            self.last_playlist_pos = 1

        def play(self, stream_url, is_playlist=False):
            return 42.0

    def fake_menu(title, final_pos, next_ep, allow_result_loop, current_ep=None):
        menu_calls.append({"current_ep": current_ep, "next_ep": next_ep, "final_pos": final_pos})
        return w.ACTION_QUIT, None

    monkeypatch.setattr(w.sys, "stdin", _TTY())
    monkeypatch.setattr(w, "PlayerRunner", _FakeRunner)
    monkeypatch.setattr(
        w, "build_season_playlist", lambda *a, **k: tmp_path / "pl.m3u"
    )
    monkeypatch.setattr(w, "_fetch_subtitles_safe", lambda *a, **k: None)
    monkeypatch.setattr(w, "_start_prewarm", lambda *a, **k: None)
    monkeypatch.setattr(w, "_select_episode", lambda *a, **k: eps[0])
    monkeypatch.setattr(w, "_post_play_menu", fake_menu)
    monkeypatch.setattr(w, "get_resume_position", lambda ih: None)
    monkeypatch.setattr(w, "save_resume_position", lambda **kw: saved.append(kw))
    monkeypatch.setattr(history_mod, "add_history_entry", lambda **k: None)

    action = w._play_series_session(
        _FakeRuntime(),
        _FakeTSClient(),
        seasons,
        "magnet:?xt=urn:btih:" + HASH_A,
        "Show",
        HASH_A,
        _base_cfg(),
        0.0,
        1,
        1,
        False,
        False,
        True,
    )
    assert action == w.ACTION_QUIT
    # Resume/history must point at the episode that actually ended (E02)
    assert [(s["season"], s["episode"], s["timestamp"]) for s in saved] == [(1, 2, 42.0)]
    assert menu_calls and menu_calls[0]["current_ep"] is eps[1]
    assert menu_calls[0]["next_ep"] is eps[2]


# ---------------------------------------------------------------------------
# Start-up latency helpers
# ---------------------------------------------------------------------------

def test_wait_for_files_checks_immediately(monkeypatch):
    from tream.core import torrserver as ts

    client = ts.TorrServerClient()
    monkeypatch.setattr(client, "get_torrent", lambda ih: {"file_stats": [{"id": 1}]})
    sleep_calls: list[float] = []
    monkeypatch.setattr(ts.time, "sleep", lambda s: sleep_calls.append(s))

    files = client.wait_for_files("abc", timeout=5.0)
    assert files == [{"id": 1}]
    assert sleep_calls == []  # metadata ready on the first check -> no sleeping


def test_prewarm_stream_swallows_errors(monkeypatch):
    from tream.commands import watch as w

    def _explode(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(w.httpx, "stream", _explode)
    w._prewarm_stream("http://127.0.0.1:1/stream")  # must not raise


def test_prewarm_stream_reads_at_most_one_chunk(monkeypatch):
    from tream.commands import watch as w

    chunks: list[int] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=65536):
            chunks.append(chunk_size)
            yield b"x" * 1024

    monkeypatch.setattr(w.httpx, "stream", lambda *a, **k: _Resp())
    w._prewarm_stream("http://127.0.0.1:8090/stream/fake")
    assert chunks == [65536]  # stops after the first chunk


def test_select_episode_forced_values(monkeypatch):
    from tream.commands import watch as w
    from tream.core.series import EpisodeInfo

    ep1 = EpisodeInfo(file_id=1, path="S01E01.mkv", season=1, episode=1, size=0, title="a")
    ep2 = EpisodeInfo(file_id=2, path="S02E05.mkv", season=2, episode=5, size=0, title="b")
    seasons = {1: [ep1], 2: [ep2]}

    monkeypatch.setattr(w, "get_resume_position", lambda ih: None)
    assert w._select_episode(seasons, 2, 5, HASH_A) is ep2
    assert w._select_episode(seasons, None, 5, HASH_A) is ep2
    assert w._select_episode(seasons, 3, 1, HASH_A) is None
    assert w._select_episode(seasons, 1, None, HASH_A) is ep1  # single episode auto-selects
