import importlib.resources
import os
from pathlib import Path
import sys
import tomllib
from typing import Any
import platformdirs
import tomli_w


def is_termux() -> bool:
    """Detect if running inside Android Termux environment."""
    return bool(os.environ.get("TERMUX_VERSION")) or Path("/data/data/com.termux").exists()


def get_config_dir() -> Path:
    """Return the configuration directory for tream across platforms."""
    if is_termux():
        return Path.home() / ".config" / "tream"
    return Path(platformdirs.user_config_dir("tream", appauthor=False))


def get_config_path() -> Path:
    """Return the path to config.toml."""
    return get_config_dir() / "config.toml"


def get_data_dir() -> Path:
    """Return the data directory for tream (e.g. downloaded binaries, TorrServer data)."""
    if is_termux():
        return Path.home() / ".local" / "share" / "tream"
    return Path(platformdirs.user_data_dir("tream", appauthor=False))


def get_history_path() -> Path:
    """Return the path to history.json."""
    return get_config_dir() / "history.json"


def get_resume_path() -> Path:
    """Return the path to resume.json."""
    return get_config_dir() / "resume.json"


def get_default_config_content() -> str:
    """Read the default configuration template packaged with tream."""
    try:
        ref = importlib.resources.files("tream.templates").joinpath("default_config.toml")
        return ref.read_text(encoding="utf-8")
    except Exception:
        fallback_path = Path(__file__).parent.parent / "templates" / "default_config.toml"
        if fallback_path.exists():
            return fallback_path.read_text(encoding="utf-8")
        return (
            "[general]\n"
            'player = "mpv"\n'
            "torrserver_port = 8090\n"
            'torrserver_binary = "torrserver"\n\n'
            "[playback]\n"
            'buffer = "256M"\n'
            "preload_cache = 30\n"
            "connections_limit = 800\n"
            "enable_dht = true\n\n"
            "[search]\n"
            'providers = ["yts", "apibay", "nyaa"]\n'
            "max_results = 20\n"
            "fuzzy = true\n\n"
            "[subtitles]\n"
            "enabled = false\n"
            'languages = ["en"]\n\n'
            "[series]\n"
            "auto_group = true\n"
            "resume = true\n"
        )


def load_config() -> dict[str, Any]:
    """Load config from disk or create default if not present, then apply env overrides."""
    config_path = get_config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        default_content = get_default_config_content()
        config_path.write_text(default_content, encoding="utf-8")
        data = tomllib.loads(default_content)
    else:
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except Exception as err:
            raise RuntimeError(f"Failed to parse config file at {config_path}: {err}") from err

    # Ensure required tables exist
    data.setdefault("general", {})
    data.setdefault("playback", {})
    data.setdefault("search", {})
    data.setdefault("subtitles", {})
    data.setdefault("series", {})
    data.setdefault("torznab", {})

    # Apply environment variable overrides (highest precedence)
    if env_buf := os.environ.get("TREAM_BUFFER"):
        data["playback"]["buffer"] = env_buf
    if env_player := os.environ.get("TREAM_PLAYER"):
        data["general"]["player"] = env_player
    if env_port := os.environ.get("TREAM_PORT"):
        try:
            data["general"]["torrserver_port"] = int(env_port)
        except ValueError:
            pass
    if env_bin := os.environ.get("TREAM_TORRSERVER_BIN"):
        data["general"]["torrserver_binary"] = env_bin

    return data


def save_config(data: dict[str, Any]) -> None:
    """Save configuration dictionary to config.toml."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = tomli_w.dumps(data)
    config_path.write_text(content, encoding="utf-8")
