import math
import os
import sys
from typing import Any
import questionary
from tream.providers.base import SearchResult
from tream.utils.logging import get_console


def _format_result_label(r: SearchResult, index: int) -> str:
    """Format single result row with provider tag, title, size, seeders, and resolution."""
    seeder_info = f"S:{r.seeders} L:{r.leechers}"
    return f"{index:2d}. [{r.provider}] {r.title} | {r.formatted_size} | {seeder_info} [{r.quality}]"


def interactive_pick_result(
    query: str,
    results: list[SearchResult],
    page_size: int = 10,
) -> SearchResult | None:
    """Present an ani-cli style paginated, clean-screen interactive picker with quality filtering."""
    if not results:
        return None

    # Discover available qualities (excluding Unknown)
    found_qualities = sorted(list({r.quality.upper() for r in results if r.quality and r.quality.upper() != "UNKNOWN"}))
    quality_filter: str | None = None
    page_idx = 0
    console = get_console()

    while True:
        # 1. Filter items by quality if specified
        if quality_filter and quality_filter != "ALL":
            active_list = [r for r in results if r.quality.upper() == quality_filter]
        else:
            active_list = results

        if not active_list:
            # Fall back to all if current filter has zero results
            quality_filter = "ALL"
            active_list = results

        total_pages = max(1, math.ceil(len(active_list) / page_size))
        page_idx = max(0, min(page_idx, total_pages - 1))

        start_idx = page_idx * page_size
        end_idx = min(start_idx + page_size, len(active_list))
        current_page_items = active_list[start_idx:end_idx]

        # 2. Clear terminal for fresh ani-cli look
        console.clear()
        console.print(f"[bold cyan]tream[/bold cyan] :: results for [bold white]\"{query}\"[/bold white]")
        filter_display = quality_filter or "ALL"
        console.print(
            f"[dim]Page {page_idx + 1} of {total_pages} ({len(active_list)} torrents) | Resolution filter: [{filter_display}][/dim]\n"
        )

        # 3. Build choices list
        choices: list[questionary.Choice] = []
        for i, r in enumerate(current_page_items, start=start_idx + 1):
            choices.append(questionary.Choice(title=_format_result_label(r, i), value=r))

        choices.append(questionary.Choice(title="---", value="__DIVIDER__", disabled=True))

        # Pagination controls
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

        # Quality filter control
        if len(found_qualities) > 1:
            choices.append(
                questionary.Choice(
                    title=f"  [Filter Resolution: currently '{filter_display}']",
                    value="__FILTER_QUALITY__",
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

        if isinstance(selection, SearchResult):
            console.clear()
            return selection
