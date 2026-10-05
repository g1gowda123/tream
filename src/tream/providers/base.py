from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from tream.utils.cache import format_bytes
from tream.utils.magnet import normalize_infohash

_QUALITY_REGEX = re.compile(r"\b(2160p|4k|1080p|720p|480p|bdrip|brrip|web-?dl|webrip|hdr)\b", re.IGNORECASE)


def extract_quality(text: str) -> str:
    """Extract resolution or quality tag from title string."""
    matches = _QUALITY_REGEX.findall(text)
    if matches:
        return matches[0].upper()
    return "Unknown"


@dataclass
class SearchResult:
    title: str
    magnet: str
    infohash: str
    size: int
    seeders: int
    leechers: int
    provider: str
    quality: str
    category: str

    def __post_init__(self) -> None:
        if self.infohash:
            try:
                self.infohash = normalize_infohash(self.infohash)
            except ValueError:
                pass
        if not self.quality or self.quality.lower() == "unknown":
            self.quality = extract_quality(self.title)
        else:
            self.quality = self.quality.upper()

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size)


class SearchProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        """Perform search query and return normalized search results."""
        raise NotImplementedError
