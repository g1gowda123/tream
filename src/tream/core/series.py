from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid
from tream.core.config import get_resume_path
from tream.utils.cache import format_bytes

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".m4v", ".ts", ".flv"}

# Episode marker regexes in descending order of specificity
_PATTERNS = [
    # S01E02 or s1e2
    re.compile(r"[sS]([0-9]{1,2})[eE]([0-9]{1,3})"),
    # 1x02 or 01x02
    re.compile(r"\b([0-9]{1,2})x([0-9]{1,3})\b"),
    # Season 1 Episode 2
    re.compile(r"\b[sS]eason\s*([0-9]{1,2})\b.*?\b(?:[eE]pisode|[eE]p)\s*\.?\s*([0-9]{1,3})\b", re.IGNORECASE),
    # Standalone Episode 02 or Ep 02 (default Season 1)
    re.compile(r"\b(?:[eE]pisode|[eE]p)\s*\.?\s*([0-9]{1,3})\b", re.IGNORECASE),
    # Standalone E02
    re.compile(r"\b[eE]([0-9]{2,3})\b"),
    # Anime release format: Title - 02 (1080p) or [Group] Title - 02.mkv
    re.compile(r"(?:^|\s+)-\s+([0-9]{1,3})(?:\s+|\[|\(|\.)"),
]


@dataclass
class EpisodeInfo:
    file_id: int
    path: str
    season: int
    episode: int
    size: int
    title: str

    @property
    def display_name(self) -> str:
        size_str = format_bytes(self.size) if self.size > 0 else ""
        size_part = f" ({size_str})" if size_str else ""
        return f"S{self.season:02d}E{self.episode:02d} - {self.title}{size_part}"


def parse_episode(filename: str, file_id: int = 0, size: int = 0) -> EpisodeInfo | None:
    """Parse episode markers (S01E01, 1x01, Episode 1, Ep01, etc.) from filename."""
    path_obj = Path(filename)
    if path_obj.suffix.lower() not in VIDEO_EXTENSIONS:
        return None

    name_without_ext = path_obj.stem
    season = 1
    episode = 1
    matched = False

    # Check S01E01
    m1 = _PATTERNS[0].search(name_without_ext)
    if m1:
        season = int(m1.group(1))
        episode = int(m1.group(2))
        matched = True
    else:
        # Check 1x01
        m2 = _PATTERNS[1].search(name_without_ext)
        if m2:
            season = int(m2.group(1))
            episode = int(m2.group(2))
            matched = True
        else:
            # Check Season X Episode Y
            m3 = _PATTERNS[2].search(name_without_ext)
            if m3:
                season = int(m3.group(1))
                episode = int(m3.group(2))
                matched = True
            else:
                # Check Ep 01 / Episode 01
                m4 = _PATTERNS[3].search(name_without_ext)
                if m4:
                    season = 1
                    episode = int(m4.group(1))
                    matched = True
                else:
                    # Check E01
                    m5 = _PATTERNS[4].search(name_without_ext)
                    if m5:
                        season = 1
                        episode = int(m5.group(1))
                        matched = True
                    else:
                        # Check Anime format: - 01
                        m6 = _PATTERNS[5].search(name_without_ext)
                        if m6:
                            season = 1
                            episode = int(m6.group(1))
                            matched = True

    if not matched:
        return None

    # Derive clean title from filename
    clean_title = re.sub(r"\[[^\]]+\]|\([^\)]+\)", "", name_without_ext)
    clean_title = clean_title.replace(".", " ").replace("_", " ").strip()

    return EpisodeInfo(
        file_id=file_id,
        path=filename,
        season=season,
        episode=episode,
        size=size,
        title=clean_title,
    )


def group_and_sort_episodes(file_stats: list[dict[str, Any]]) -> dict[int, list[EpisodeInfo]]:
    """Group video files by season and sort by episode number."""
    seasons: dict[int, list[EpisodeInfo]] = {}
    video_files_fallback: list[EpisodeInfo] = []

    for file_stat in file_stats:
        path = file_stat.get("path") or file_stat.get("Path") or ""
        file_id = int(file_stat.get("id", file_stat.get("Id", 0)))
        size = int(file_stat.get("length", file_stat.get("Length", 0)))

        ext = Path(path).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            continue

        ep = parse_episode(path, file_id=file_id, size=size)
        if ep:
            seasons.setdefault(ep.season, []).append(ep)
        else:
            # Keep as fallback if regex didn't catch specific format
            stem = Path(path).stem
            video_files_fallback.append(
                EpisodeInfo(
                    file_id=file_id,
                    path=path,
                    season=1,
                    episode=0,
                    size=size,
                    title=stem,
                )
            )

    # If no files matched regex, assign sequential episode numbers
    if not seasons and video_files_fallback:
        video_files_fallback.sort(key=lambda x: x.path)
        for idx, item in enumerate(video_files_fallback, start=1):
            item.episode = idx
        seasons[1] = video_files_fallback

    # Sort each season by episode number
    for s_num in seasons:
        seasons[s_num].sort(key=lambda x: (x.episode, x.path))

    return seasons


def build_season_playlist(
    episodes: list[EpisodeInfo],
    start_index: int,
    base_stream_url_getter: Any,
) -> Path:
    """Build a temporary M3U playlist starting from the selected episode for auto-advance."""
    sub_list = episodes[start_index:]
    playlist_path = Path(tempfile.gettempdir()) / f"tream_season_{uuid.uuid4().hex[:8]}.m3u"

    lines = ["#EXTM3U"]
    for ep in sub_list:
        url = base_stream_url_getter(ep.file_id, Path(ep.path).name)
        lines.append(f"#EXTINF:-1,{ep.display_name}")
        lines.append(url)

    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return playlist_path


def load_resume_data() -> dict[str, Any]:
    """Load resume dictionary from ~/.config/tream/resume.json."""
    path = get_resume_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_resume_position(
    infohash: str,
    season: int,
    episode: int,
    file_id: int,
    timestamp: float,
    title: str = "",
) -> None:
    """Save the last watched episode and playback position for a series."""
    path = get_resume_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_resume_data()

    data[infohash.lower()] = {
        "season": season,
        "episode": episode,
        "file_id": file_id,
        "timestamp": timestamp,
        "title": title,
    }

    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_resume_position(infohash: str) -> dict[str, Any] | None:
    """Retrieve saved resume state for a torrent infohash."""
    data = load_resume_data()
    return data.get(infohash.lower())
