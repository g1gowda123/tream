import httpx
from tream.providers.base import SearchProvider, SearchResult, extract_quality
from tream.utils.magnet import build_magnet

_DEFAULT_API_URL = "https://apibay.org/q.php"


def _map_category(cat_id: str) -> str:
    try:
        cid = int(cat_id)
        if 200 <= cid < 300:
            if cid in (205, 208):
                return "TV"
            return "Movies"
        if 100 <= cid < 200:
            return "Audio"
        if 300 <= cid < 400:
            return "Applications"
        if 400 <= cid < 500:
            return "Games"
    except (ValueError, TypeError):
        pass
    return "Other"


class ApibayProvider(SearchProvider):
    name: str = "apibay"

    def __init__(self, api_url: str = _DEFAULT_API_URL, timeout: float = 8.0) -> None:
        self.api_url = api_url
        self.timeout = timeout

    async def search(self, query: str) -> list[SearchResult]:
        params = {"q": query, "cat": ""}
        headers = {"User-Agent": "Mozilla/5.0 tream/0.1"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(self.api_url, params=params, headers=headers)
                if resp.status_code != 200:
                    return []
                items = resp.json()
        except Exception:
            return []

        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", ""))
            infohash = str(item.get("info_hash", "")).strip().lower()
            name = str(item.get("name", "")).strip()

            # Apibay sentinel for no results found
            if item_id == "0" or not infohash or infohash == "0" * 40:
                continue

            try:
                seeders = int(item.get("seeders", 0))
            except (ValueError, TypeError):
                seeders = 0

            try:
                leechers = int(item.get("leechers", 0))
            except (ValueError, TypeError):
                leechers = 0

            try:
                size = int(item.get("size", 0))
            except (ValueError, TypeError):
                size = 0

            magnet = build_magnet(infohash, name)
            category = _map_category(str(item.get("category", "")))
            quality = extract_quality(name)

            results.append(
                SearchResult(
                    title=name,
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
