from datetime import datetime
import json
from pathlib import Path
from typing import Any
import questionary
from tream.core.config import get_history_path
from tream.utils.logging import status

MAX_HISTORY_ENTRIES = 200


def format_seconds(seconds: float) -> str:
    """Format duration in seconds into HH:MM:SS or MM:SS."""
    sec = int(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def load_history() -> list[dict[str, Any]]:
    """Load watch history from ~/.config/tream/history.json."""
    path = get_history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def add_history_entry(
    title: str,
    magnet: str,
    infohash: str,
    position_seconds: float,
    is_series: bool = False,
    season: int = 1,
    episode: int = 1,
) -> None:
    """Record or update stream entry in history, capped at 200 items."""
    path = get_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    history = load_history()

    clean_hash = infohash.lower().strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Remove existing entry for the same hash if present
    history = [item for item in history if item.get("infohash", "").lower() != clean_hash]

    new_entry = {
        "title": title,
        "magnet": magnet,
        "infohash": clean_hash,
        "position": position_seconds,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "timestamp": now_str,
    }

    history.insert(0, new_entry)
    history = history[:MAX_HISTORY_ENTRIES]

    try:
        path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception:
        pass


def run_history(buffer_override: str | None = None, player_override: str | None = None) -> None:
    """Present watch history picker and re-stream selected item."""
    history = load_history()
    if not history:
        status("no watch history found")
        return

    choices: list[questionary.Choice] = []
    for item in history:
        title = item.get("title", "Unknown")
        pos = item.get("position", 0.0)
        time_str = item.get("timestamp", "")
        is_series = item.get("is_series", False)

        label = f"{title}"
        if is_series:
            s = item.get("season", 1)
            e = item.get("episode", 1)
            label += f" [S{s:02d}E{e:02d}]"

        label += f" | {format_seconds(pos)} | {time_str}"
        choices.append(questionary.Choice(title=label, value=item))

    choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        selected = questionary.select(
            "Select from watch history:",
            choices=choices,
            use_indicator=True,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return

    if not selected:
        return

    from tream.commands.watch import resume_from_history
    resume_from_history(selected, buffer_override=buffer_override, player_override=player_override)
