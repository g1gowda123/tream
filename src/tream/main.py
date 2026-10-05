import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Annotated, Optional
import typer
from tream.commands.config_cmd import config_app
from tream.commands.download import run_download
from tream.commands.history import run_history
from tream.commands.update import run_update
from tream.commands.watch import watch_stream
from tream.core.config import load_config
from tream.core.search import search_all
from tream.core.torrserver import stop_torrserver
from tream.utils.logging import init_logging, status

app = typer.Typer(
    name="tream",
    help="Fast, frictionless torrent streaming and download tool.",
    add_completion=False,
    no_args_is_help=False,
    context_settings={"help_option_names": ["--help"]},
)

app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query_args: Annotated[
        Optional[list[str]],
        typer.Argument(help="Search query and optional arguments"),
    ] = None,
    series: Annotated[
        bool,
        typer.Option("-s", "--series", help="Search series, auto-group episodes, pick episode to stream"),
    ] = False,
    download: Annotated[
        bool,
        typer.Option("-d", "--download", help="Download torrent to specified folder"),
    ] = False,
    history: Annotated[
        bool,
        typer.Option("-h", "--history", help="Show watch history and let user re-stream something"),
    ] = False,
    update: Annotated[
        bool,
        typer.Option("-U", "--update", help="Update tream and its dependencies"),
    ] = False,
    buffer: Annotated[
        Optional[str],
        typer.Option("-b", "--buffer", help="Override buffer size for this run (e.g. 512M, 1G)"),
    ] = None,
    player: Annotated[
        Optional[str],
        typer.Option("-p", "--player", help="Override player for this run (mpv or vlc)"),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output search results as JSON (non-interactive)"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Write debug logs to cache directory"),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    init_logging(verbose=verbose)

    args = list(query_args or [])

    # If first positional argument is "config", delegate to config_app
    if args and args[0] == "config":
        try:
            config_app(args[1:])
        except SystemExit as e:
            raise typer.Exit(code=e.code if isinstance(e.code, int) else 0)
        return

    if update:
        run_update()
        raise typer.Exit(code=0)

    if history:
        run_history(buffer_override=buffer, player_override=player)
        raise typer.Exit(code=0)

    if not args:
        status("no search query provided. Run 'tream --help' for usage.")
        raise typer.Exit(code=1)

    try:
        if download:
            if len(args) >= 2:
                target_folder = Path(args[-1])
                search_query = " ".join(args[:-1])
            else:
                target_folder = Path.cwd()
                search_query = " ".join(args)

            run_download(
                query=search_query,
                target_folder=target_folder,
                buffer_override=buffer,
            )
            return

        search_query = " ".join(args)

        if as_json:
            cfg = load_config()
            results = asyncio.run(search_all(search_query, cfg))
            raw_data = [asdict(r) for r in results]
            print(json.dumps(raw_data, indent=2))
            return

        watch_stream(
            query=search_query,
            is_series=series,
            buffer_override=buffer,
            player_override=player,
        )
    except KeyboardInterrupt:
        status("\ncleaning up...")
        stop_torrserver()
        sys.exit(130)


if __name__ == "__main__":
    app()
