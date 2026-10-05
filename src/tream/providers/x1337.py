import asyncio
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import httpx
from tream.providers.base import SearchProvider, SearchResult, extract_quality
from tream.utils.cache import parse_buffer_size
from tream.utils.magnet import parse_magnet

_DEFAULT_DOMAIN = "https://1337x.to"
_FALLBACK_DOMAINS = ["https://1337x.st", "https://1337x.ws", "https://1337x.eu"]


class X1337Provider(SearchProvider):
    name: str = "1337x"

    def __init__(self, base_url: str = _DEFAULT_DOMAIN, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _fetch_magnet(self, client: httpx.AsyncClient, detail_url: str) -> tuple[str, str]:
        """Fetch detail page to extract magnet URI and infohash."""
        try:
            resp = await client.get(detail_url)
            if resp.status_code != 200:
                return "", ""
            soup = BeautifulSoup(resp.text, "html.parser")
            magnet_link = soup.find("a", href=re.compile(r"^magnet:\?"))
            if magnet_link and magnet_link.get("href"):
                magnet_uri = str(magnet_link["href"])
                info = parse_magnet(magnet_uri)
                return magnet_uri, info.infohash

            infohash_elem = soup.find("div", class_="infohash-box") or soup.find("span", class_="infohash")
            if infohash_elem and infohash_elem.text:
                ih = infohash_elem.text.strip().lower()
                return "", ih
        except Exception:
            pass
        return "", ""

    async def search(self, query: str) -> list[SearchResult]:
        search_path = f"/search/{quote_plus(query)}/1/"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        domains = [self.base_url] + [d for d in _FALLBACK_DOMAINS if d != self.base_url]
        html = ""
        active_domain = self.base_url

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            for domain in domains:
                try:
                    resp = await client.get(f"{domain}{search_path}")
                    if resp.status_code == 200 and "table-list" in resp.text:
                        html = resp.text
                        active_domain = domain
                        break
                except Exception:
                    continue

            if not html:
                return []

            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", class_="table-list")
            if not table:
                return []

            tbody = table.find("tbody") or table
            rows = tbody.find_all("tr")

            extracted: list[dict] = []
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 5:
                    continue

                name_col = cols[0]
                links = name_col.find_all("a")
                # Usually second link in td.name is the torrent link (first is icon or category)
                torrent_link = None
                for a in links:
                    href = a.get("href", "")
                    if href.startswith("/torrent/"):
                        torrent_link = a
                        break

                if not torrent_link:
                    continue

                title = torrent_link.text.strip()
                detail_url = f"{active_domain}{torrent_link['href']}"

                try:
                    seeders = int(cols[1].text.strip().replace(",", ""))
                except (ValueError, TypeError):
                    seeders = 0

                try:
                    leechers = int(cols[2].text.strip().replace(",", ""))
                except (ValueError, TypeError):
                    leechers = 0

                size_text = cols[4].text.strip() if len(cols) > 4 else ""
                size = 0
                if size_text:
                    # 1337x format: "1.4 GB" or "500 MB" (sometimes contains seed count text)
                    size_match = re.search(r"([0-9]+(?:\.[0-9]+)?\s*[a-zA-Z]+)", size_text)
                    if size_match:
                        try:
                            size = parse_buffer_size(size_match.group(1))
                        except ValueError:
                            size = 0

                extracted.append(
                    {
                        "title": title,
                        "detail_url": detail_url,
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size,
                    }
                )

            # Limit detail page fetches to top 15 to stay fast
            candidates = extracted[:15]
            sem = asyncio.Semaphore(5)

            async def resolve_item(item: dict) -> SearchResult | None:
                async with sem:
                    magnet, infohash = await self._fetch_magnet(client, item["detail_url"])
                    if not magnet and not infohash:
                        return None
                    if not magnet and infohash:
                        from tream.utils.magnet import build_magnet
                        magnet = build_magnet(infohash, item["title"])

                    return SearchResult(
                        title=item["title"],
                        magnet=magnet,
                        infohash=infohash,
                        size=item["size"],
                        seeders=item["seeders"],
                        leechers=item["leechers"],
                        provider=self.name,
                        quality=extract_quality(item["title"]),
                        category="Video",
                    )

            results = await asyncio.gather(*(resolve_item(item) for item in candidates))
            return [r for r in results if r is not None]
