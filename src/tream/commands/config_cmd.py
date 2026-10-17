import json
import os
from pathlib import Path
import platformdirs
import subprocess
import sys
import tomllib
from typing import Any
import typer
from rich.table import Table
from tream.core.config import (
    get_config_path,
    get_data_dir,
    get_default_config_content,
    get_history_path,
    get_resume_path,
    load_config,
    save_config,
)
from tream.core.search import PROVIDER_NAMES, unknown_provider_names
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import get_console, status

config_app = typer.Typer(name="config", help="Manage tream configuration.")

_ENV_OVERRIDES: dict[str, str] = {
    "TREAM_BUFFER": "playback.buffer",
    "TREAM_PLAYER": "general.player",
    "TREAM_PORT": "general.torrserver_port",
    "TREAM_TORRSERVER_BIN": "general.torrserver_binary",
}


def _parse_value(val: str) -> Any:
    val_lower = val.lower().strip()
    if val_lower == "true":
        return True
    if val_lower == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    if val.startswith("[") and val.endswith("]"):
        items = [i.strip().strip("'\"") for i in val[1:-1].split(",") if i.strip()]
        return items
    return val


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_key(key: str, value: Any) -> str | None:
    """Validate a known config key. Returns an error message or None when valid."""
    if key == "playback.buffer":
        try:
            parse_buffer_size(value)
        except (ValueError, TypeError) as err:
            return f"invalid buffer size: {err}"
    elif key == "general.torrserver_port":
        if not _is_int(value) or not (1 <= value <= 65535):
            return "port must be an integer between 1 and 65535"
    elif key == "general.player":
        if not isinstance(value, str) or not value.strip():
            return "player must be a non-empty executable name or path"
    elif key == "search.providers":
        if not isinstance(value, list) or not value:
            return "providers must be a non-empty list, e.g. [yts, apibay, nyaa]"
        unknown = unknown_provider_names([str(v) for v in value])
        if unknown:
            return f"unknown provider(s): {', '.join(unknown)} (valid: {', '.join(PROVIDER_NAMES)})"
    elif key == "search.max_results":
        if not _is_int(value) or value < 1:
            return "max_results must be a positive integer"
    elif key == "search.min_seeders":
        if not _is_int(value) or value < 0:
            return "min_seeders must be a non-negative integer"
    elif key in ("playback.preload_cache", "playback.connections_limit"):
        if not _is_int(value) or value < 1:
            return f"{key.split('.')[-1]} must be a positive integer"
    elif key in ("playback.enable_dht", "playback.prewarm", "search.fuzzy", "series.auto_group", "series.resume", "subtitles.enabled"):
        if not isinstance(value, bool):
            return "value must be true or false"
    return None


def _flatten(config: dict[str, Any]) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for section in sorted(config):
        value = config[section]
        if isinstance(value, dict):
            for key in sorted(value):
                rows.append((f"{section}.{key}", value[key]))
        else:
            rows.append((section, value))
    return rows


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value == "":
        return '""'
    return str(value)


@config_app.command("get")
def config_get(key: str = typer.Argument(..., help="Config key in 'section.key' format")) -> None:
    """Read a configuration value."""
    cfg = load_config()
    parts = key.split(".", 1)
    if len(parts) == 2:
        section, subkey = parts
        val = cfg.get(section, {}).get(subkey)
    else:
        val = cfg.get(key)

    if val is None:
        status(f"Key '{key}' not found in configuration")
        raise typer.Exit(code=1)

    print(_format_value(val))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key in 'section.key' format"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a configuration value."""
    cfg = load_config()
    parts = key.split(".", 1)
    parsed = _parse_value(value)

    error = validate_key(key, parsed)
    if error:
        status(f"refusing to set {key}: {error}")
        raise typer.Exit(code=1)

    if len(parts) == 2:
        section, subkey = parts
        cfg.setdefault(section, {})[subkey] = parsed
    else:
        cfg[key] = parsed

    save_config(cfg)
    status(f"set {key} = {_format_value(parsed)}")


@config_app.command("show")
def config_show(
    as_json: bool = typer.Option(False, "--json", help="Print effective config as JSON"),
) -> None:
    """Show the effective configuration (including environment overrides)."""
    cfg = load_config()

    if as_json:
        print(json.dumps(cfg, indent=2, sort_keys=True))
        return

    table = Table(title="tream :: effective configuration", title_style="bold cyan", header_style="bold")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value")
    table.add_column("Source", style="dim")

    env_active = {path: var for var, path in _ENV_OVERRIDES.items() if os.environ.get(var)}
    for key, value in _flatten(cfg):
        source = f"env {env_active[key]}" if key in env_active else "config/default"
        table.add_row(key, _format_value(value), source)

    console = get_console()
    console.print(table)
    console.print(f"[dim]file: {get_config_path()}[/dim]")


@config_app.command("path")
def config_paths() -> None:
    """Print all tream file and directory locations."""
    log_dir = Path(platformdirs.user_cache_dir("tream")) / "logs"
    rows = [
        ("config file", get_config_path()),
        ("data dir", get_data_dir()),
        ("history file", get_history_path()),
        ("resume file", get_resume_path()),
        ("log dir", log_dir),
    ]
    width = max(len(label) for label, _ in rows)
    for label, path in rows:
        print(f"{label.ljust(width)} : {path}")


@config_app.command("edit")
def config_edit() -> None:
    """Open configuration file in default editor."""
    cfg_path = get_config_path()
    if not cfg_path.exists():
        load_config()

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        if sys.platform == "win32":
            editor = "notepad"
        else:
            for ed in ["nano", "vim", "vi"]:
                if subprocess.run(["which", ed], capture_output=True).returncode == 0:
                    editor = ed
                    break
            if not editor:
                editor = "nano"

    subprocess.run([editor, str(cfg_path)])


@config_app.command("reset")
def config_reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Restore the configuration file to defaults."""
    if not yes:
        import questionary

        try:
            confirmed = questionary.confirm(
                f"Reset {get_config_path()} to defaults?", default=False
            ).ask()
        except (KeyboardInterrupt, EOFError):
            confirmed = False
        if not confirmed:
            status("reset cancelled")
            raise typer.Exit(code=0)

    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(get_default_config_content(), encoding="utf-8")
    status(f"configuration reset to defaults ({cfg_path})")


@config_app.command("validate")
def config_validate() -> None:
    """Validate configuration file syntax and schema."""
    cfg_path = get_config_path()
    if not cfg_path.exists():
        status("configuration file does not exist yet (using defaults)")
        return

    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as err:
        status(f"configuration syntax error: {err}")
        raise typer.Exit(code=1)

    errors: list[str] = []
    for key, value in _flatten(data):
        error = validate_key(key, value)
        if error:
            errors.append(f"{key}: {error}")

    if errors:
        for error in errors:
            status(f"invalid {error}")
        raise typer.Exit(code=1)

    status("configuration is valid")
