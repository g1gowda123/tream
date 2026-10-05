import atexit
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Any
import httpx
from tream.core.config import get_data_dir, is_termux, load_config
from tream.utils.cache import parse_buffer_size
from tream.utils.logging import get_logger, status

_torrserver_proc: subprocess.Popen[bytes] | None = None
_spawned_by_tream: bool = False
_signals_registered: bool = False


def find_torrserver_binary(configured_path: str = "torrserver") -> Path | None:
    """Find TorrServer executable across PATH, Termux, and local data directory."""
    logger = get_logger()

    # 1. Configured path or environment variable
    if configured_path:
        direct = Path(configured_path).expanduser()
        if direct.is_file() and os.access(direct, os.X_OK):
            return direct
        which_path = shutil.which(configured_path)
        if which_path:
            return Path(which_path)

    # 2. Standard PATH check
    bin_name = "torrserver.exe" if sys.platform == "win32" else "torrserver"
    which_path = shutil.which(bin_name)
    if which_path:
        return Path(which_path)

    # 3. Termux specific path
    if is_termux():
        termux_bin = Path("/data/data/com.termux/files/usr/bin/torrserver")
        if termux_bin.is_file() and os.access(termux_bin, os.X_OK):
            return termux_bin

    # 4. Common Linux / Unix system paths
    common_system_paths = [
        Path("/usr/local/bin") / bin_name,
        Path("/usr/bin") / bin_name,
        Path.home() / ".local" / "bin" / bin_name,
    ]
    for p in common_system_paths:
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 5. User data directory inside tream
    data_bin = get_data_dir() / "bin" / bin_name
    if data_bin.is_file() and os.access(data_bin, os.X_OK):
        return data_bin

    logger.debug("TorrServer binary not found in standard paths")
    return None


def is_port_in_use(port: int) -> bool:
    """Check if TorrServer is actively responding on the given port."""
    url = f"http://127.0.0.1:{port}/echo"
    try:
        resp = httpx.get(url, timeout=0.8)
        return resp.status_code == 200
    except Exception:
        return False


def _register_cleanup_signals() -> None:
    global _signals_registered
    if _signals_registered:
        return

    atexit.register(stop_torrserver)

    def _sig_handler(signum: int, frame: Any) -> None:
        stop_torrserver()
        sys.exit(128 + signum)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, _sig_handler)
    except (ValueError, AttributeError):
        pass

    _signals_registered = True


def spawn_torrserver(port: int = 8090, binary_path: Path | None = None) -> None:
    """Spawn TorrServer background process if not already running."""
    global _torrserver_proc, _spawned_by_tream
    logger = get_logger()

    if is_port_in_use(port):
        logger.debug("TorrServer already active on port %d", port)
        return

    bin_path = binary_path or find_torrserver_binary()
    if not bin_path:
        raise FileNotFoundError(
            "TorrServer binary not found. Please install TorrServer or specify its path in config."
        )

    ts_data_dir = get_data_dir() / "torrserver"
    ts_data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [str(bin_path), "--port", str(port), "--path", str(ts_data_dir)]
    logger.debug("Spawning TorrServer: %s", " ".join(cmd))

    creation_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if sys.platform == "win32":
        creation_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation_kwargs["start_new_session"] = True

    try:
        _torrserver_proc = subprocess.Popen(cmd, **creation_kwargs)
        _spawned_by_tream = True
    except Exception as err:
        raise RuntimeError(f"Failed to start TorrServer subprocess: {err}") from err

    _register_cleanup_signals()

    # Wait up to 10 seconds for the HTTP service to start
    start_time = time.monotonic()
    while time.monotonic() - start_time < 10.0:
        if _torrserver_proc.poll() is not None:
            code = _torrserver_proc.returncode
            _torrserver_proc = None
            _spawned_by_tream = False
            raise RuntimeError(f"TorrServer process exited unexpectedly with code {code}")

        if is_port_in_use(port):
            logger.debug("TorrServer successfully started on port %d", port)
            return

        time.sleep(0.2)

    stop_torrserver()
    raise TimeoutError(f"TorrServer failed to respond on port {port} within 10 seconds")


def stop_torrserver() -> None:
    """Terminate the spawned TorrServer subprocess if tream started it."""
    global _torrserver_proc, _spawned_by_tream
    if not _spawned_by_tream or not _torrserver_proc:
        return

    proc = _torrserver_proc
    _torrserver_proc = None
    _spawned_by_tream = False

    if proc.poll() is not None:
        return

    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()

        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:
        pass


class TorrServerClient:
    """Client for interacting with TorrServer REST API."""

    def __init__(self, port: int = 8090, timeout: float = 10.0) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.timeout = timeout

    def configure_settings(
        self,
        buffer_bytes: int,
        preload_cache: int = 30,
        connections_limit: int = 800,
        enable_dht: bool = True,
        reader_read_ahead: int = 95,
    ) -> bool:
        """Configure TorrServer memory cache and network settings."""
        url = f"{self.base_url}/settings"
        payload = {
            "action": "set",
            "sets": {
                "CacheSize": buffer_bytes,
                "PreloadCache": preload_cache,
                "ConnectionsLimit": connections_limit,
                "UseDisk": False,
                "DisableDHT": not enable_dht,
                "ReaderReadAHead": reader_read_ahead,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as err:
            get_logger().warning("Failed to configure TorrServer settings: %s", err)
            return False

    def add_torrent(self, link: str, title: str = "") -> dict[str, Any]:
        """Add torrent or magnet to TorrServer."""
        url = f"{self.base_url}/torrents"
        payload = {
            "action": "add",
            "link": link,
            "title": title,
            "poster": "",
            "data": "",
            "save_to_db": False,
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"TorrServer add torrent failed: HTTP {resp.status_code} {resp.text}")
            return resp.json()

    def get_torrent(self, infohash: str) -> dict[str, Any]:
        """Fetch current status and file details for a torrent."""
        url = f"{self.base_url}/torrents"
        payload = {"action": "get", "hash": infohash.lower()}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"TorrServer get torrent failed: HTTP {resp.status_code} {resp.text}")
            return resp.json()

    def drop_torrent(self, infohash: str) -> None:
        """Drop torrent from TorrServer memory."""
        url = f"{self.base_url}/torrents"
        payload = {"action": "drop", "hash": infohash.lower()}
        try:
            with httpx.Client(timeout=3.0) as client:
                client.post(url, json=payload)
        except Exception:
            pass

    def wait_for_files(self, infohash: str, timeout: float = 35.0) -> list[dict[str, Any]]:
        """Poll TorrServer until torrent metadata is loaded and file list is available."""
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            try:
                info = self.get_torrent(infohash)
                files = info.get("file_stats", [])
                if files:
                    return files
            except Exception:
                pass
            time.sleep(1.0)

        raise TimeoutError("Timed out waiting for torrent metadata and file list")

    def get_stream_url(self, infohash: str, file_id: int | None = None, filename: str = "") -> str:
        """Build playback stream URL for a file in the torrent."""
        clean_name = filename.strip() or "video"
        from urllib.parse import quote
        safe_name = quote(clean_name)
        if file_id is not None:
            return f"{self.base_url}/stream/{safe_name}?link={infohash.lower()}&index={file_id}&play"
        return f"{self.base_url}/stream/{safe_name}?link={infohash.lower()}&play"

    def get_m3u_url(self, infohash: str) -> str:
        """Build M3U playlist URL for all files in the torrent."""
        return f"{self.base_url}/stream/fname?link={infohash.lower()}&m3u"
