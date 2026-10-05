from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from typing import Generator
import platformdirs
from rich.console import Console

_stderr_console = Console(stderr=True, highlight=False)
_logger = logging.getLogger("tream")


def get_console() -> Console:
    """Return stderr console for non-piped status outputs."""
    return _stderr_console


def status(msg: str) -> None:
    """Print an ani-cli style status line to stderr."""
    _stderr_console.print(msg)


@contextmanager
def spinner(msg: str) -> Generator[None, None, None]:
    """Show a transient spinner on stderr while an operation executes."""
    with _stderr_console.status(msg, spinner="dots"):
        yield


def init_logging(verbose: bool = False) -> logging.Logger:
    """Configure tream logging. In verbose mode, logs are written to cache dir."""
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    _logger.handlers.clear()

    if verbose:
        log_dir = Path(platformdirs.user_cache_dir("tream")) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "tream.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
        )
        file_handler.setFormatter(formatter)
        _logger.addHandler(file_handler)
        _logger.debug("Verbose logging initialized at %s", log_file)
    else:
        null_handler = logging.NullHandler()
        _logger.addHandler(null_handler)

    return _logger


def get_logger() -> logging.Logger:
    """Get the tream logger instance."""
    return _logger
