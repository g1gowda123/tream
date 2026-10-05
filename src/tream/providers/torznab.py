import xml.etree.ElementTree as ET
import httpx
from tream.providers.base import SearchProvider, SearchResult, extract_quality
from tream.utils.magnet import build_magnet, parse_magnet

_TORZNAB_NS = {"torznab": "http://torznab.com/schemas/2015/feed"}


class TorznabProvider(SearchProvider):
    name: str = "torznab"

    def __init__(self, api_url: str = "", api_key: str = "", timeout: float = 10.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str) -> list[SearchResult]:
        if not self.api_url:
            return []

        # Support base Jackett/Prowlarr torznab URLs
        endpoint = f"{self.api_url}/api" if not self.api_url.endswith("/api") else self.api_url
        params = {"t": "search", "q": query}
        if self.api_key:
            params["apikey"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(endpoint, params=params)
                if resp.status_code != 200:
                    return []
                xml_data = resp.text
        except Exception:
            return []

        try:
            root = ET.fromstring(xml_data)
        except Exception:
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        results: list[SearchResult] = []
        for item in channel.findall("item"):
            title_elem = item.find("title")
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else "Unknown"

            infohash = ""
            magnet = ""
            seeders = 0
            leechers = 0
            size = 0

            size_elem = item.find("size")
            if size_elem is not None and size_elem.text:
                try:
                    size = int(size_elem.text.strip())
                except ValueError:
                    size = 0

            for attr in item.findall("torznab:attr", _TORZNAB_NS):
                name = attr.get("name", "")
                val = attr.get("value", "")
                if name == "seeders":
                    try:
                        seeders = int(val)
                    except ValueError:
                        pass
                elif name == "peers":
                    try:
                        leechers = int(val)
                    except ValueError:
                        pass
                elif name == "infohash":
                    infohash = val.strip().lower()
                elif name == "magneturl":
                    magnet = val.strip()

            enclosure = item.find("enclosure")
            if enclosure is not None:
                enc_url = enclosure.get("url", "")
                if enc_url.startswith("magnet:?"):
                    magnet = enc_url
                if not size and enclosure.get("length"):
                    try:
                        size = int(enclosure.get("length", 0))
                    except ValueError:
                        pass

            if magnet and not infohash:
                try:
                    info = parse_magnet(magnet)
                    infohash = info.infohash
                except Exception:
                    pass

            if not magnet and infohash:
                magnet = build_magnet(infohash, title)

            if not infohash:
                continue

            results.append(
                SearchResult(
                    title=title,
                    magnet=magnet,
                    infohash=infohash,
                    size=size,
                    seeders=seeders,
                    leechers=leechers,
                    provider=self.name,
                    quality=extract_quality(title),
                    category="Torznab",
                )
            )

        return results
