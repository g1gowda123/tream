from pathlib import Path
import subprocess
import sys
import tream
from tream.utils.logging import status


def _detect_git_repo() -> Path | None:
    """Traverse parent directories to check if source lives in a git repository."""
    curr = Path(__file__).resolve().parent
    for _ in range(6):
        if (curr / ".git").is_dir():
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent
    return None


def run_update() -> None:
    """Detect install method (git, pipx, pip) and perform self-update."""
    current_version = getattr(tream, "__version__", "0.1.0")
    status(f"current version: {current_version}")

    git_root = _detect_git_repo()

    if git_root:
        status("git installation detected, running git pull...")
        res = subprocess.run(["git", "pull"], cwd=git_root, capture_output=True, text=True)
        if res.returncode != 0 and "no tracking information" in res.stderr.lower():
            # Try pulling from origin with current branch name
            branch_res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=git_root,
                capture_output=True,
                text=True,
            )
            branch = branch_res.stdout.strip() or "main"
            res = subprocess.run(["git", "pull", "origin", branch], cwd=git_root, capture_output=True, text=True)

        if res.returncode != 0:
            status(f"git pull failed: {res.stderr.strip()}")
            return

        status("reinstalling in editable mode...")
        pip_cmd = [sys.executable, "-m", "pip", "install", "-e", "."]
        res = subprocess.run(pip_cmd, cwd=git_root, capture_output=True, text=True)
        if res.returncode != 0:
            status(f"pip install failed: {res.stderr.strip()}")
            return
    elif "pipx" in sys.prefix or "pipx" in sys.executable:
        status("pipx installation detected, upgrading tream...")
        res = subprocess.run(["pipx", "upgrade", "tream"], capture_output=True, text=True)
        if res.returncode != 0:
            status(f"pipx upgrade failed: {res.stderr.strip()}")
            return
    else:
        status("pip installation detected, upgrading package...")
        pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "tream-cli"]
        res = subprocess.run(pip_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            status(f"pip upgrade failed: {res.stderr.strip()}")
            return

    # Check updated version if available
    try:
        from importlib.metadata import version
        new_version = version("tream")
    except Exception:
        new_version = current_version

    status(f"updated version: {new_version}")
