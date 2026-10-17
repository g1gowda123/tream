from datetime import datetime
import json
import sys
from typing import Any
import questionary
from rich.table import Table
from tream.core.config import get_history_path
from tream.utils.logging import get_console, status

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


def clear_history() -> int:
    """Delete all watch history entries. Returns the number of removed entries."""
    history = load_history()
    path = get_history_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError as err:
        status(f"failed to clear history: {err}")
        return -1
    if history:
        status(f"cleared {len(history)} history entr{'y' if len(history) == 1 else 'ies'}")
    else:
        status("history is already empty")
    return len(history)


def _entry_label(item: dict[str, Any]) -> str:
    title = item.get("title", "Unknown")
    label = title
    if item.get("is_series", False):
        label += f" [S{item.get('season', 1):02d}E{item.get('episode', 1):02d}]"
    label += f" | {format_seconds(float(item.get('position', 0.0)))} | {item.get('timestamp', '')}"
    return label


def print_history_table(history: list[dict[str, Any]]) -> None:
    """Print watch history as a table for non-interactive usage."""
    console = get_console()
    table = Table(title="tream :: watch history", title_style="bold cyan", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", overflow="fold", max_width=70)
    table.add_column("Progress", justify="right")
    table.add_column("Watched", style="dim")

    for i, item in enumerate(history, start=1):
        label = item.get("title", "Unknown")
        if item.get("is_series", False):
            label += f" [S{item.get('season', 1):02d}E{item.get('episode', 1):02d}]"
        table.add_row(
            str(i),
            label,
            format_seconds(float(item.get("position", 0.0))),
            item.get("timestamp", ""),
        )
    console.print(table)


def run_history(
    buffer_override: str | None = None,
    player_override: str | None = None,
    verbose: bool = False,
    once: bool = False,
) -> int:
    """Present watch history picker and re-stream selected item. Returns an exit code."""
    history = load_history()
    if not history:
        status("no watch history found")
        return 0

    if not sys.stdin.isatty():
        print_history_table(history)
        status("no interactive terminal; run in a terminal to resume an entry")
        return 0

    choices: list[questionary.Choice] = [
        questionary.Choice(title=_entry_label(item), value=item) for item in history
    ]
    choices.append(questionary.Choice(title="[Cancel]", value=None))

    try:
        selected = questionary.select(
            "Select from watch history:",
            choices=choices,
            use_indicator=True,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return 0

    if not selected:
        return 0

    from tream.commands.watch import resume_from_history

    return resume_from_history(
        selected,
        buffer_override=buffer_override,
        player_override=player_override,
        verbose=verbose,
        once=once,
    )
