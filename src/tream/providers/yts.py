import httpx
from tream.providers.base import SearchProvider, SearchResult
from tream.utils.magnet import build_magnet

_DEFAULT_API_URL = "https://yts.mx/api/v2/list_movies.json"


class YTSProvider(SearchProvider):
    name: str = "yts"

    def __init__(self, api_url: str = _DEFAULT_API_URL, timeout: float = 8.0) -> None:
        self.api_url = api_url
        self.timeout = timeout

    async def search(self, query: str) -> list[SearchResult]:
        params = {"query_term": query, "limit": 20}
        headers = {"User-Agent": "Mozilla/5.0 tream/0.1"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(self.api_url, params=params, headers=headers)
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []

        movies = data.get("data", {}).get("movies", [])
        if not movies:
            return []

        results: list[SearchResult] = []
        for movie in movies:
            title = movie.get("title_long") or movie.get("title") or "Unknown"
            torrents = movie.get("torrents", [])
            for tor in torrents:
                infohash = tor.get("hash", "")
                if not infohash:
                    continue
                quality = tor.get("quality", "Unknown")
                tor_type = tor.get("type", "")
                full_title = f"{title} [{quality}] {tor_type}".strip()
                size = tor.get("size_bytes", 0)
                seeds = tor.get("seeds", 0)
                peers = tor.get("peers", 0)
                magnet = build_magnet(infohash, full_title)

                results.append(
                    SearchResult(
                        title=full_title,
                        magnet=magnet,
                        infohash=infohash,
                        size=size,
                        seeders=seeds,
                        leechers=peers,
                        provider=self.name,
                        quality=quality,
                        category="Movies",
                    )
                )

        return results
