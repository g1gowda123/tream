import asyncio
import sys
import tempfile
from typing import Any
from tream import __version__
from tream.core.config import get_config_dir, get_data_dir, get_config_path, load_config
from tream.core.player import detect_player
from tream.core.search import PROVIDER_NAMES, get_enabled_providers, unknown_provider_names
from tream.core.torrserver import find_torrserver_binary, is_port_in_use, spawn_torrserver, stop_torrserver
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import get_console

_STATUS_ICONS = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}


def _check_python() -> tuple[str, str, str]:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        return "ok", f"Python {version}", ""
    return "fail", f"Python {version}", "tream requires Python 3.11 or newer"


def _check_config(cfg: dict[str, Any]) -> tuple[str, str, str]:
    config_path = get_config_path()
    if not config_path.is_file():
        return "warn", "config file not found", "it will be created with defaults on first run"

    try:
        parse_buffer_size(cfg.get("playback", {}).get("buffer", "256M"))
    except ValueError as err:
        return "fail", "invalid playback.buffer", str(err)

    port = cfg.get("general", {}).get("torrserver_port", 8090)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        return "fail", "invalid general.torrserver_port", f"got {port!r}"

    player = cfg.get("general", {}).get("player", "mpv")
    if not isinstance(player, str) or not player.strip():
        return "fail", "invalid general.player", f"got {player!r}"

    return "ok", f"config valid ({config_path})", ""


def _check_dirs() -> tuple[str, str, str]:
    for label, directory in (("config", get_config_dir()), ("data", get_data_dir())):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, delete=True):
                pass
        except OSError as err:
            return "fail", f"{label} directory is not writable", f"{directory}: {err}"
    return "ok", "config/data directories writable", str(get_config_dir())


def _check_torrserver(cfg: dict[str, Any]) -> tuple[str, str, str]:
    binary = find_torrserver_binary(cfg.get("general", {}).get("torrserver_binary", "torrserver"))
    if not binary:
        return (
            "fail",
            "TorrServer binary not found",
            "install it (AUR: torrserver-bin) or set general.torrserver_binary / TREAM_TORRSERVER_BIN",
        )

    port = cfg.get("general", {}).get("torrserver_port", 8090)
    if is_port_in_use(port):
        return "ok", f"TorrServer responding on port {port}", str(binary)

    try:
        spawn_torrserver(port=port, binary_path=binary)
        stop_torrserver()
    except Exception as err:
        return "fail", f"TorrServer failed to start on port {port}", str(err)

    return "ok", "TorrServer starts successfully (test instance stopped)", str(binary)


def _check_player(cfg: dict[str, Any]) -> tuple[str, str, str]:
    try:
        player = detect_player(cfg.get("general", {}).get("player"))
    except FileNotFoundError as err:
        return "fail", "no media player found", str(err)
    return "ok", f"media player found ({player})", ""


async def _probe_provider(provider: Any) -> int:
    """Return result count for a quick probe, or -1 when unreachable."""
    try:
        results = await asyncio.wait_for(provider.search("ubuntu 22.04"), timeout=8.0)
        return len(results)
    except Exception:
        return -1


async def _probe_all(providers: list[Any]) -> list[int]:
    return list(await asyncio.gather(*(_probe_provider(p) for p in providers)))


def _check_providers(cfg: dict[str, Any], network: bool) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    names = cfg.get("search", {}).get("providers", ["yts", "apibay", "nyaa"])
    unknown = unknown_provider_names(names)
    if unknown:
        checks.append(("fail", f"unknown provider(s): {', '.join(unknown)}", f"valid providers: {', '.join(PROVIDER_NAMES)}"))

    providers = get_enabled_providers(cfg)
    if not providers:
        checks.append(("fail", "no search providers enabled", "run 'tream config set search.providers [yts, apibay, nyaa]'"))
        return checks

    checks.append(("ok", f"{len(providers)} search provider(s) enabled: {', '.join(p.name for p in providers)}", ""))

    if network:
        results = asyncio.run(_probe_all(providers))
        for provider, count in zip(providers, results):
            if count < 0:
                checks.append(("warn", f"provider '{provider.name}' unreachable", "network issue or site down"))
            else:
                checks.append(("ok", f"provider '{provider.name}' reachable ({count} results)", ""))
    else:
        checks.append(("warn", "provider connectivity not tested", "re-run with 'tream doctor --network'"))

    return checks


def _check_subtitles(cfg: dict[str, Any]) -> tuple[str, str, str]:
    if not cfg.get("subtitles", {}).get("enabled", False):
        return "ok", "subtitles disabled", "enable with 'tream config set subtitles.enabled true'"
    try:
        import subliminal  # type: ignore  # noqa: F401

        return "ok", "subtitles enabled (subliminal installed)", ""
    except ImportError:
        return (
            "warn",
            "subtitles enabled but subliminal is not installed",
            "install with 'pip install \"tream[subtitles]\"' or 'pipx inject tream subliminal'",
        )


def run_doctor(network: bool = False) -> int:
    """Run environment diagnostics and report what is misconfigured. Returns an exit code."""
    console = get_console()

    try:
        cfg = load_config()
        config_error = None
    except Exception as err:
        cfg = {}
        config_error = str(err)

    checks: list[tuple[str, str, str]] = [_check_python()]
    if config_error:
        checks.append(("fail", "config file failed to parse", config_error))
    else:
        checks.append(_check_config(cfg))

    checks.append(_check_dirs())
    checks.append(_check_torrserver(cfg))
    checks.append(_check_player(cfg))
    checks.extend(_check_providers(cfg, network))
    checks.append(_check_subtitles(cfg))

    console.print(f"\n[bold cyan]tream doctor[/bold cyan] [dim]v{__version__}[/dim]\n")
    for state, label, detail in checks:
        line = f"  {_STATUS_ICONS[state]} {label}"
        if detail:
            line += f"\n      [dim]{detail}[/dim]"
        console.print(line)

    failures = sum(1 for state, _, _ in checks if state == "fail")
    warnings = sum(1 for state, _, _ in checks if state == "warn")
    oks = len(checks) - failures - warnings

    console.print("")
    console.print(f"[green]{oks} ok[/green], [yellow]{warnings} warning(s)[/yellow], [red]{failures} failure(s)[/red]")
    if failures:
        console.print("[dim]fix the failures above, then re-run 'tream doctor'[/dim]")
    return 1 if failures else 0
