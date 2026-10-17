import asyncio
from dataclasses import asdict
import json
import sys
from pathlib import Path
from typing import Annotated, Optional
import typer
from tream import __version__
from tream.commands.config_cmd import config_app
from tream.commands.doctor import run_doctor
from tream.commands.download import download_magnet, resolve_download_target, run_download
from tream.commands.history import clear_history, load_history, run_history
from tream.commands.update import run_update
from tream.commands.watch import list_results, stream_magnet, watch_stream
from tream.core.config import load_config
from tream.core.search import search_all, unknown_provider_names
from tream.core.torrserver import stop_torrserver
from tream.utils.logging import init_logging, status
from tream.utils.magnet import looks_like_torrent_ref

app = typer.Typer(
    name="tream",
    help="Fast, frictionless torrent streaming and download tool.",
    add_completion=False,
    no_args_is_help=False,
    # Allow options after the query words, e.g. `tream "dune 2" --buffer 512M`.
    # `--help` is handled manually so `tream config ... --help` shows the config help.
    context_settings={"help_option_names": [], "allow_interspersed_args": True},
)

app.add_typer(config_app, name="config")


def _split_providers(values: Optional[list[str]]) -> Optional[list[str]]:
    """Flatten repeated/comma-separated --provider values into a unique list."""
    if not values:
        return None
    out: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip().lower()
            if name and name not in out:
                out.append(name)
    return out or None


def _prompt_query() -> str:
    """Interactively ask for a search query when none was given."""
    import questionary

    try:
        answer = questionary.text(
            "Search:", instruction="(movie, series, or anime — Ctrl+C to exit)"
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return ""
    return (answer or "").strip()


def _fail(message: str) -> None:
    status(message)
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query_args: Annotated[
        Optional[list[str]],
        typer.Argument(help="Search query, magnet/infohash, or command arguments"),
    ] = None,
    series: Annotated[
        bool,
        typer.Option("-s", "--series", help="Search series, group episodes, pick an episode to stream"),
    ] = False,
    download: Annotated[
        bool,
        typer.Option("-d", "--download", help="Download torrent files to a folder"),
    ] = False,
    history: Annotated[
        bool,
        typer.Option("-h", "--history", help="Show watch history and re-stream something"),
    ] = False,
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Clear watch history (use together with -h)"),
    ] = False,
    update: Annotated[
        bool,
        typer.Option("-U", "--update", help="Update tream and its dependencies"),
    ] = False,
    doctor: Annotated[
        bool,
        typer.Option("--doctor", help="Run environment diagnostics (TorrServer, player, providers)"),
    ] = False,
    network: Annotated[
        bool,
        typer.Option("--network", help="With --doctor: also test connectivity to each provider"),
    ] = False,
    list_only: Annotated[
        bool,
        typer.Option("-l", "--list", help="Print ranked search results as a table (no prompts)"),
    ] = False,
    once: Annotated[
        bool,
        typer.Option("--once", help="Exit after playback instead of showing the follow-up menu"),
    ] = False,
    magnet: Annotated[
        Optional[str],
        typer.Option("--magnet", help="Stream a magnet URI or infohash directly (skips search)"),
    ] = None,
    pick: Annotated[
        Optional[int],
        typer.Option("--pick", help="Select the Nth ranked result non-interactively (1-based)"),
    ] = None,
    season: Annotated[
        Optional[int],
        typer.Option("--season", help="With -s: play this season non-interactively"),
    ] = None,
    episode: Annotated[
        Optional[int],
        typer.Option("--episode", help="With -s: play this episode non-interactively"),
    ] = None,
    providers: Annotated[
        Optional[list[str]],
        typer.Option("-P", "--provider", help="Only use these providers for this run (repeatable / comma-separated)"),
    ] = None,
    min_seeders: Annotated[
        Optional[int],
        typer.Option("--min-seeders", help="Hide results with fewer seeders than this"),
    ] = None,
    buffer: Annotated[
        Optional[str],
        typer.Option("-b", "--buffer", help="Override buffer size for this run (e.g. 512M, 1G)"),
    ] = None,
    player: Annotated[
        Optional[str],
        typer.Option("-p", "--player", help="Override player for this run (mpv or vlc)"),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("-o", "--output", help="With -d: folder to save downloads into"),
    ] = None,
    all_files: Annotated[
        bool,
        typer.Option("-a", "--all-files", help="With -d: download every file without prompting"),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="With -d: re-download files even if they already exist"),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output search results or history as JSON (non-interactive)"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Write debug logs to the cache directory"),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("-V", "--version", help="Show the tream version and exit"),
    ] = False,
    show_help: Annotated[
        bool,
        typer.Option("--help", help="Show this message and exit"),
    ] = False,
) -> None:
    if version:
        print(f"tream {__version__}")
        raise typer.Exit(code=0)

    if ctx.invoked_subcommand is not None:
        return

    init_logging(verbose=verbose)

    args = list(query_args or [])

    # Typer's variadic positional argument swallows subcommand names, so route
    # `tream config ...` manually to the config CLI.
    if args and args[0] == "config":
        delegated = args[1:]
        if show_help:
            delegated = delegated + ["--help"]
        elif not delegated:
            delegated = ["--help"]
        try:
            config_app(delegated)
        except SystemExit as e:
            raise typer.Exit(code=e.code if isinstance(e.code, int) else 0)
        return

    if show_help:
        print(ctx.get_help())
        raise typer.Exit(code=0)

    for name, value in (("--pick", pick), ("--season", season), ("--episode", episode)):
        if value is not None and value < 1:
            _fail(f"{name} must be 1 or greater")
    if min_seeders is not None and min_seeders < 0:
        _fail("--min-seeders must be 0 or greater")

    provider_override = _split_providers(providers)
    if provider_override:
        unknown = unknown_provider_names(provider_override)
        if unknown:
            _fail(f"unknown provider(s): {', '.join(unknown)}")

    if doctor:
        raise typer.Exit(code=run_doctor(network=network))

    if update:
        raise typer.Exit(code=run_update())

    if history:
        if clear:
            removed = clear_history()
            raise typer.Exit(code=0 if removed >= 0 else 1)
        if as_json:
            print(json.dumps(load_history(), indent=2))
            raise typer.Exit(code=0)
        raise typer.Exit(
            code=run_history(
                buffer_override=buffer, player_override=player, verbose=verbose, once=once
            )
        )

    if clear:
        _fail("--clear must be used together with -h/--history")

    if not download:
        if output is not None:
            _fail("-o/--output can only be used together with -d/--download")
        if all_files or overwrite:
            _fail("-a/--all-files and --overwrite can only be used together with -d/--download")

    # Direct magnet / infohash streaming (explicit --magnet, or a lone magnet/hash argument)
    magnet_ref = magnet
    if magnet_ref is None and len(args) == 1 and looks_like_torrent_ref(args[0]):
        magnet_ref = args[0]
        args = []

    if magnet_ref is not None:
        if download:
            query, folder = resolve_download_target(args, explicit=output)
            rc = download_magnet(
                magnet_ref,
                title=query,
                target_folder=folder,
                buffer_override=buffer,
                all_files=all_files,
                overwrite=overwrite,
            )
            raise typer.Exit(code=rc)

        rc = stream_magnet(
            magnet_ref,
            title=" ".join(args),
            is_series=series,
            buffer_override=buffer,
            player_override=player,
            forced_season=season,
            forced_episode=episode,
            verbose=verbose,
            once=once,
        )
        raise typer.Exit(code=rc)

    if not args:
        if not sys.stdin.isatty():
            _fail("no search query provided. Run 'tream --help' for usage.")
        query = _prompt_query()
        if not query:
            raise typer.Exit(code=1)
        args = [query]

    try:
        if download:
            query, folder = resolve_download_target(args, explicit=output)
            if not query:
                _fail("no search query provided alongside the download folder")
            rc = run_download(
                query=query,
                target_folder=folder,
                buffer_override=buffer,
                all_files=all_files,
                overwrite=overwrite,
                verbose=verbose,
                pick_index=pick,
                providers=provider_override,
                min_seeders=min_seeders,
            )
            raise typer.Exit(code=rc)

        query = " ".join(args)

        if as_json:
            cfg = load_config()
            results = asyncio.run(
                search_all(query, cfg, providers_override=provider_override, min_seeders=min_seeders)
            )
            print(json.dumps([asdict(r) for r in results], indent=2))
            raise typer.Exit(code=0)

        if list_only:
            raise typer.Exit(
                code=list_results(query, providers=provider_override, min_seeders=min_seeders)
            )

        rc = watch_stream(
            query=query,
            is_series=series,
            buffer_override=buffer,
            player_override=player,
            verbose=verbose,
            pick_index=pick,
            season=season,
            episode=episode,
            providers=provider_override,
            min_seeders=min_seeders,
            once=once,
        )
        raise typer.Exit(code=rc)
    except KeyboardInterrupt:
        status("\ncleaning up...")
        stop_torrserver()
        sys.exit(130)


if __name__ == "__main__":
    app()
