import math
import re
import sys
import questionary
from rich.table import Table
from tream.providers.base import SearchResult
from tream.utils.logging import get_console, status

SORT_MODES: dict[str, str] = {
    "seeders": "Most seeders",
    "size": "Largest size",
    "quality": "Best resolution",
    "title": "Title (A-Z)",
}

_QUALITY_RANK_RE = re.compile(r"(\d{3,4})\s*P\b")
_QUALITY_ALIASES = {"4K": 2160, "UHD": 2160, "FHD": 1080, "HD": 720}


def quality_rank(quality: str) -> int:
    """Map a quality string (e.g. '1080P', '4K') to a numeric resolution rank."""
    q = (quality or "").strip().upper()
    if not q:
        return 0
    if q in _QUALITY_ALIASES:
        return _QUALITY_ALIASES[q]
    match = _QUALITY_RANK_RE.search(q)
    if match:
        return int(match.group(1))
    return 0


def sort_results(results: list[SearchResult], mode: str = "seeders") -> list[SearchResult]:
    """Sort search results by the given mode without mutating the input list."""
    if mode == "title":
        return sorted(results, key=lambda r: (r.title.lower(), r.seeders), reverse=False)
    if mode == "quality":
        return sorted(
            results,
            key=lambda r: (quality_rank(r.quality), r.seeders, r.size),
            reverse=True,
        )
    if mode == "size":
        return sorted(results, key=lambda r: (r.size, r.seeders), reverse=True)
    return sorted(results, key=lambda r: (r.seeders, r.size), reverse=True)


def _format_result_label(r: SearchResult, index: int) -> str:
    """Format single result row with provider tag, title, size, seeders, and resolution."""
    seeder_info = f"S:{r.seeders} L:{r.leechers}"
    return f"{index:2d}. [{r.provider}] {r.title} | {r.formatted_size} | {seeder_info} [{r.quality}]"


def print_results_table(query: str, results: list[SearchResult]) -> None:
    """Print a clean, script-friendly table of search results (no prompts)."""
    console = get_console()
    if not results:
        status(f"no results found for \"{query}\"")
        return

    table = Table(
        title=f"tream :: results for \"{query}\"",
        title_style="bold cyan",
        header_style="bold",
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", overflow="fold", max_width=80)
    table.add_column("Provider", style="magenta")
    table.add_column("Quality", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Seeders", justify="right", style="green")
    table.add_column("Leechers", justify="right", style="red")

    for i, r in enumerate(results, start=1):
        table.add_row(
            str(i),
            r.title,
            r.provider,
            r.quality,
            r.formatted_size,
            str(r.seeders),
            str(r.leechers),
        )

    console.print(table)
    console.print("[dim]Pick one with: tream --pick N \"query\" (or add -d to download)[/dim]")


def pick_by_index(results: list[SearchResult], index: int) -> SearchResult:
    """Return the Nth result (1-based). Raises IndexError when out of range."""
    if index < 1 or index > len(results):
        raise IndexError(f"--pick {index} is out of range (1-{len(results)})")
    return results[index - 1]


def interactive_pick_result(
    query: str,
    results: list[SearchResult],
    page_size: int = 10,
    initial_sort: str = "seeders",
) -> SearchResult | None:
    """Present an ani-cli style paginated interactive picker with resolution filtering and sorting."""
    if not results:
        return None

    if not sys.stdin.isatty():
        status(
            "no interactive terminal detected. Re-run with --pick N to select a result "
            "(use --list to print results first)."
        )
        return None

    found_qualities = sorted(
        {r.quality.upper() for r in results if r.quality and r.quality.upper() != "UNKNOWN"},
        key=quality_rank,
        reverse=True,
    )
    quality_filter: str | None = None
    sort_mode = initial_sort if initial_sort in SORT_MODES else "seeders"
    page_idx = 0
    console = get_console()

    while True:
        ordered = sort_results(results, sort_mode)

        if quality_filter and quality_filter != "ALL":
            active_list = [r for r in ordered if r.quality.upper() == quality_filter]
        else:
            active_list = ordered

        if not active_list:
            quality_filter = "ALL"
            active_list = ordered

        total_pages = max(1, math.ceil(len(active_list) / page_size))
        page_idx = max(0, min(page_idx, total_pages - 1))

        start_idx = page_idx * page_size
        end_idx = min(start_idx + page_size, len(active_list))
        current_page_items = active_list[start_idx:end_idx]

        console.clear()
        console.print(f"[bold cyan]tream[/bold cyan] :: results for [bold white]\"{query}\"[/bold white]")
        filter_display = quality_filter or "ALL"
        console.print(
            f"[dim]Page {page_idx + 1} of {total_pages} ({len(active_list)} torrents) | "
            f"Resolution: [{filter_display}] | Sort: [{SORT_MODES[sort_mode]}][/dim]\n"
        )

        choices: list[questionary.Choice] = []
        for i, r in enumerate(current_page_items, start=start_idx + 1):
            choices.append(questionary.Choice(title=_format_result_label(r, i), value=r))

        choices.append(questionary.Choice(title="---", value="__DIVIDER__", disabled=True))

        if page_idx + 1 < total_pages:
            choices.append(
                questionary.Choice(
                    title=f"  [>> Next Page ({page_idx + 2}/{total_pages})]",
                    value="__NEXT__",
                )
            )

        if page_idx > 0:
            choices.append(
                questionary.Choice(
                    title=f"  [<< Previous Page ({page_idx}/{total_pages})]",
                    value="__PREV__",
                )
            )

        if len(found_qualities) > 1:
            choices.append(
                questionary.Choice(
                    title=f"  [Filter Resolution: currently '{filter_display}']",
                    value="__FILTER_QUALITY__",
                )
            )

        choices.append(
            questionary.Choice(
                title=f"  [Sort: {SORT_MODES[sort_mode]}]",
                value="__SORT__",
            )
        )

        choices.append(questionary.Choice(title="  [Cancel / Exit]", value=None))

        try:
            selection = questionary.select(
                "Select result to stream:",
                choices=choices,
                use_indicator=True,
            ).ask()
        except (KeyboardInterrupt, EOFError):
            console.clear()
            return None

        if selection is None:
            console.clear()
            return None

        if selection == "__NEXT__":
            page_idx += 1
            continue

        if selection == "__PREV__":
            page_idx -= 1
            continue

        if selection == "__FILTER_QUALITY__":
            q_choices = [questionary.Choice(title="Show All Resolutions", value="ALL")]
            for q in found_qualities:
                count = sum(1 for r in results if r.quality.upper() == q)
                q_choices.append(questionary.Choice(title=f"{q} ({count} torrents)", value=q))
            q_choices.append(questionary.Choice(title="[Back]", value="__BACK__"))

            try:
                new_q = questionary.select("Select resolution:", choices=q_choices).ask()
            except (KeyboardInterrupt, EOFError):
                new_q = "__BACK__"

            if new_q and new_q != "__BACK__":
                quality_filter = new_q
                page_idx = 0
            continue

        if selection == "__SORT__":
            s_choices = [
                questionary.Choice(title=label, value=mode) for mode, label in SORT_MODES.items()
            ]
            s_choices.append(questionary.Choice(title="[Back]", value="__BACK__"))

            try:
                new_sort = questionary.select("Sort results by:", choices=s_choices).ask()
            except (KeyboardInterrupt, EOFError):
                new_sort = "__BACK__"

            if new_sort and new_sort != "__BACK__":
                sort_mode = new_sort
                page_idx = 0
            continue

        if isinstance(selection, SearchResult):
            console.clear()
            return selection
