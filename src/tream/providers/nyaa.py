import xml.etree.ElementTree as ET
import httpx
from tream.providers.base import SearchProvider, SearchResult, extract_quality
from tream.utils.cache import parse_buffer_size
from tream.utils.magnet import build_magnet

_DEFAULT_RSS_URL = "https://nyaa.si/"
_NYAA_NS = {"nyaa": "https://nyaa.si/xmlns/nyaa"}


class NyaaProvider(SearchProvider):
    name: str = "nyaa"

    def __init__(self, base_url: str = _DEFAULT_RSS_URL, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str) -> list[SearchResult]:
        url = f"{self.base_url}/?page=rss"
        params = {"q": query, "c": "0_0", "f": "0"}
        headers = {"User-Agent": "Mozilla/5.0 tream/0.1"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code != 200:
                    return []
                xml_content = resp.text
        except Exception:
            return []

        try:
            root = ET.fromstring(xml_content)
        except Exception:
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        results: list[SearchResult] = []
        for item in channel.findall("item"):
            title_elem = item.find("title")
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

            hash_elem = item.find("nyaa:infoHash", _NYAA_NS)
            if hash_elem is None or not hash_elem.text:
                continue
            infohash = hash_elem.text.strip().lower()

            seeders_elem = item.find("nyaa:seeders", _NYAA_NS)
            seeders = int(seeders_elem.text.strip()) if seeders_elem is not None and seeders_elem.text else 0

            leechers_elem = item.find("nyaa:leechers", _NYAA_NS)
            leechers = int(leechers_elem.text.strip()) if leechers_elem is not None and leechers_elem.text else 0

            size_elem = item.find("nyaa:size", _NYAA_NS)
            size_str = size_elem.text.strip() if size_elem is not None and size_elem.text else ""
            size = 0
            if size_str:
                try:
                    size = parse_buffer_size(size_str)
                except ValueError:
                    size = 0

            category_elem = item.find("nyaa:category", _NYAA_NS)
            category = category_elem.text.strip() if category_elem is not None and category_elem.text else "Anime"

            magnet = build_magnet(infohash, title)
            quality = extract_quality(title)

            results.append(
                SearchResult(
                    title=title,
                    magnet=magnet,
                    infohash=infohash,
                    size=size,
                    seeders=seeders,
                    leechers=leechers,
                    provider=self.name,
                    quality=quality,
                    category=category,
                )
            )

        return results
