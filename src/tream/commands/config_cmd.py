import os
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any
import typer
from tream.core.config import get_config_path, load_config, save_config
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import status

config_app = typer.Typer(name="config", help="Manage tream configuration.")


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

    print(val)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key in 'section.key' format"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a configuration value."""
    cfg = load_config()
    parts = key.split(".", 1)
    parsed = _parse_value(value)

    if len(parts) == 2:
        section, subkey = parts
        cfg.setdefault(section, {})[subkey] = parsed
    else:
        cfg[key] = parsed

    save_config(cfg)
    status(f"set {key} = {parsed}")


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

    # Validate playback buffer
    buf = data.get("playback", {}).get("buffer", "256M")
    try:
        parse_buffer_size(buf)
    except Exception as err:
        status(f"invalid playback.buffer: {err}")
        raise typer.Exit(code=1)

    # Validate torrserver port
    port = data.get("general", {}).get("torrserver_port", 8090)
    if not isinstance(port, int) or port < 1 or port > 65535:
        status(f"invalid general.torrserver_port: {port}")
        raise typer.Exit(code=1)

    status("configuration is valid")
