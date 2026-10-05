import os
from pathlib import Path
import tempfile
from typing import Any
import httpx
from tream.core.config import load_config
from tream.utils.logging import get_logger, status


def fetch_subtitles(
    title: str,
    languages: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> Path | None:
    """Attempt to fetch subtitles for a title using subliminal or OpenSubtitles REST API."""
    cfg = config or load_config()
    sub_cfg = cfg.get("subtitles", {})
    if not sub_cfg.get("enabled", False):
        return None

    target_languages = languages or sub_cfg.get("languages", ["en"])
    logger = get_logger()
    logger.debug("Attempting subtitle search for %r in languages %s", title, target_languages)

    # 1. Try subliminal if installed
    try:
        from babelfish import Language  # type: ignore
        from subliminal import Video, download_best_subtitles, save_subtitles  # type: ignore

        video = Video.fromname(title)
        langs = {Language.fromietf(l) for l in target_languages}
        best_subs = download_best_subtitles([video], langs)
        if best_subs.get(video):
            temp_dir = Path(tempfile.gettempdir()) / "tream_subs"
            temp_dir.mkdir(parents=True, exist_ok=True)
            saved = save_subtitles(video, best_subs[video], directory=str(temp_dir))
            if saved:
                sub_path = Path(saved[0].path if hasattr(saved[0], "path") else str(saved[0]))
                if sub_path.exists():
                    return sub_path
    except Exception as err:
        logger.debug("Subliminal subtitle fetch skipped: %s", err)

    # 2. Try OpenSubtitles REST API as fallback
    try:
        clean_query = title.replace(".", " ").strip()
        lang_code = target_languages[0] if target_languages else "en"
        url = f"https://api.opensubtitles.com/api/v1/subtitles"
        headers = {"User-Agent": "tream v0.1", "Accept": "application/json"}
        params = {"query": clean_query, "languages": lang_code}

        with httpx.Client(timeout=4.0, headers=headers) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                if items:
                    files = items[0].get("attributes", {}).get("files", [])
                    if files:
                        file_id = files[0].get("file_id")
                        dl_resp = client.post(
                            "https://api.opensubtitles.com/api/v1/download",
                            json={"file_id": file_id},
                            headers=headers,
                        )
                        if dl_resp.status_code == 200:
                            dl_link = dl_resp.json().get("link")
                            if dl_link:
                                sub_content = client.get(dl_link).content
                                sub_file = Path(tempfile.gettempdir()) / f"tream_{file_id}.srt"
                                sub_file.write_bytes(sub_content)
                                return sub_file
    except Exception as err:
        logger.debug("OpenSubtitles API fallback skipped: %s", err)

    return None
