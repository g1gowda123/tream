import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid
from tream.core.config import is_termux, load_config
from tream.utils.cache import buffer_to_vlc_caching_ms
from tream.utils.logging import get_logger, status

PLAYER_CANDIDATES = ["mpv", "vlc"]


def detect_player(preference: str | None = None) -> str:
    """Detect available media player (mpv or vlc), respecting preferences."""
    if preference:
        p_clean = preference.strip().lower()
        if shutil.which(p_clean) or (sys.platform == "win32" and shutil.which(f"{p_clean}.exe")):
            return p_clean
        # Check direct path if user supplied full binary path
        if Path(preference).is_file():
            return preference

    # Auto-detect mpv first, then vlc
    for p in PLAYER_CANDIDATES:
        if shutil.which(p) or (sys.platform == "win32" and shutil.which(f"{p}.exe")):
            return p

    # Android / Termux checks
    if is_termux():
        for termux_cmd in ["mpv", "termux-open-url"]:
            if shutil.which(termux_cmd):
                return termux_cmd

    raise FileNotFoundError(
        "No media player found. Please install MPV (recommended) or VLC and ensure it is in your PATH."
    )


class MPVIPCClient:
    """Minimal JSON IPC client for MPV across Unix domain sockets and Windows named pipes."""

    def __init__(self, ipc_path: str) -> None:
        self.ipc_path = ipc_path

    def get_time_pos(self) -> float | None:
        """Query MPV for current playback position in seconds."""
        cmd = json.dumps({"command": ["get_property", "time-pos"]}) + "\n"

        if sys.platform == "win32":
            try:
                # Windows named pipe access
                with open(self.ipc_path, "r+b", buffering=0) as pipe:
                    pipe.write(cmd.encode("utf-8"))
                    line = pipe.readline().decode("utf-8")
                    data = json.loads(line)
                    if data.get("error") == "success" and data.get("data") is not None:
                        return float(data["data"])
            except Exception:
                return None
        else:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect(self.ipc_path)
                sock.sendall(cmd.encode("utf-8"))
                response = b""
                while b"\n" not in response:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                sock.close()
                for line in response.decode("utf-8", errors="ignore").splitlines():
                    try:
                        data = json.loads(line)
                        if data.get("error") == "success" and data.get("data") is not None:
                            return float(data["data"])
                    except Exception:
                        pass
            except Exception:
                return None

        return None


class PlayerRunner:
    """Manages spawning player process, buffer flags, IPC tracking, and resume."""

    def __init__(
        self,
        player_binary: str,
        buffer_bytes: int,
        title: str = "",
        resume_seconds: float = 0.0,
        subtitle_file: Path | None = None,
    ) -> None:
        self.player_binary = player_binary
        self.buffer_bytes = buffer_bytes
        self.title = title
        self.resume_seconds = max(0.0, resume_seconds)
        self.subtitle_file = subtitle_file
        self.last_position = self.resume_seconds
        self.logger = get_logger()

    def play(self, stream_url: str, is_playlist: bool = False) -> float:
        """Launch media player and block until playback ends, returning final position."""
        player_name = Path(self.player_binary).stem.lower()

        if "mpv" in player_name:
            return self._play_mpv(stream_url, is_playlist)
        elif "vlc" in player_name:
            return self._play_vlc(stream_url, is_playlist)
        else:
            # Fallback player invocation
            cmd = [self.player_binary, stream_url]
            proc = subprocess.Popen(cmd)
            proc.wait()
            return 0.0

    def _play_mpv(self, stream_url: str, is_playlist: bool) -> float:
        unique_id = uuid.uuid4().hex[:8]
        if sys.platform == "win32":
            ipc_target = rf"\\.\pipe\tream_mpv_{unique_id}"
        else:
            ipc_target = str(Path(tempfile.gettempdir()) / f"tream_mpv_{unique_id}.sock")

        cmd: list[str] = [
            self.player_binary,
            "--ytdl=no",
            "--cache=yes",
            f"--demuxer-max-bytes={self.buffer_bytes}",
            f"--demuxer-max-back-bytes={self.buffer_bytes}",
            f"--stream-buffer-size={self.buffer_bytes}",
            f"--input-ipc-server={ipc_target}",
        ]

        if self.title:
            cmd.append(f"--title=tream - {self.title}")

        if self.resume_seconds > 0:
            cmd.append(f"--start={int(self.resume_seconds)}")

        if self.subtitle_file and self.subtitle_file.is_file():
            cmd.append(f"--sub-file={self.subtitle_file}")

        if is_playlist:
            cmd.append(f"--playlist={stream_url}")
        else:
            cmd.append(stream_url)

        self.logger.debug("Launching MPV: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)

        ipc_client = MPVIPCClient(ipc_target)
        stop_polling = threading.Event()

        def _poll_position() -> None:
            # Wait for socket to be created
            time.sleep(1.0)
            while not stop_polling.is_set():
                pos = ipc_client.get_time_pos()
                if pos is not None and pos > 0:
                    self.last_position = pos
                time.sleep(1.5)

        poller = threading.Thread(target=_poll_position, daemon=True)
        poller.start()

        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            stop_polling.set()
            # Final attempt to query position before tearing down
            final_pos = ipc_client.get_time_pos()
            if final_pos is not None and final_pos > 0:
                self.last_position = final_pos

            # Cleanup Unix socket file if needed
            if sys.platform != "win32":
                try:
                    Path(ipc_target).unlink(missing_ok=True)
                except Exception:
                    pass

        return self.last_position

    def _play_vlc(self, stream_url: str, is_playlist: bool) -> float:
        caching_ms = buffer_to_vlc_caching_ms(self.buffer_bytes)

        # VLC handles playlists reliably via .m3u files on disk
        temp_m3u: Path | None = None
        target_path = stream_url

        if is_playlist or not stream_url.startswith("http"):
            target_path = stream_url
        else:
            temp_dir = Path(tempfile.gettempdir())
            temp_m3u = temp_dir / f"tream_vlc_{uuid.uuid4().hex[:8]}.m3u"
            content = f"#EXTM3U\n#EXTINF:-1,{self.title or 'tream stream'}\n{stream_url}\n"
            temp_m3u.write_text(content, encoding="utf-8")
            target_path = str(temp_m3u)

        cmd: list[str] = [
            self.player_binary,
            f"--file-caching={caching_ms}",
            f"--network-caching={caching_ms}",
            f"--sout-mux-caching={caching_ms}",
        ]

        if self.resume_seconds > 0:
            cmd.append(f"--start-time={int(self.resume_seconds)}")

        if self.subtitle_file and self.subtitle_file.is_file():
            cmd.append(f"--sub-file={self.subtitle_file}")

        cmd.append(target_path)

        self.logger.debug("Launching VLC: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)

        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            if temp_m3u and temp_m3u.exists():
                try:
                    temp_m3u.unlink(missing_ok=True)
                except Exception:
                    pass

        # VLC position without RC socket is approximated
        return self.last_position
