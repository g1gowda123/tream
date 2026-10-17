import asyncio
import re
from typing import Any
from tream.core.config import load_config
from tream.providers.apibay import ApibayProvider
from tream.providers.base import SearchProvider, SearchResult
from tream.providers.nyaa import NyaaProvider
from tream.providers.torznab import TorznabProvider
from tream.providers.x1337 import X1337Provider
from tream.providers.yts import YTSProvider
from tream.utils.fuzzy import fuzzy_score
from tream.utils.logging import get_logger

PROVIDER_REGISTRY: dict[str, type[SearchProvider]] = {
    "yts": YTSProvider,
    "apibay": ApibayProvider,
    "1337x": X1337Provider,
    "nyaa": NyaaProvider,
    "torznab": TorznabProvider,
}

PROVIDER_NAMES: list[str] = sorted(PROVIDER_REGISTRY.keys())

_SEASON_RE = re.compile(
    r"(?:\bseason\s*([0-9]{1,2})\b|\bs([0-9]{1,2})(?:e[0-9]{1,3}\b|\b)|\b([0-9]{1,2})(?:nd|rd|th|st)\s*season\b)",
    re.IGNORECASE,
)


def _check_season_mismatch(query: str, title: str) -> bool:
    """Check if query specifies a season number and candidate title specifies a different one."""
    q_matches = _SEASON_RE.findall(query.lower())
    if not q_matches:
        return False

    q_season = None
    for m in q_matches:
        num_str = m[0] or m[1] or m[2]
        if num_str:
            q_season = int(num_str)
            break

    if q_season is None:
        return False

    t_matches = _SEASON_RE.findall(title.lower())
    if not t_matches:
        return False

    for m in t_matches:
        num_str = m[0] or m[1] or m[2]
        if num_str:
            t_season = int(num_str)
            # Allow multi-season batches like "Season 1 + 2" or "Season 1-3"
            if "1 + 2" in title or "1-2" in title or "1 - 2" in title:
                continue
            if t_season != q_season:
                return True

    return False


def create_provider(name: str, config: dict[str, Any]) -> SearchProvider | None:
    cls = PROVIDER_REGISTRY.get(name.lower())
    if not cls:
        return None

    if name.lower() == "torznab":
        torznab_cfg = config.get("torznab", {})
        url = torznab_cfg.get("url", "")
        key = torznab_cfg.get("api_key", "")
        if not url:
            return None
        return TorznabProvider(api_url=url, api_key=key)

    return cls()


def get_enabled_providers(
    config: dict[str, Any] | None = None,
    providers_override: list[str] | None = None,
) -> list[SearchProvider]:
    cfg = config or load_config()
    if providers_override:
        provider_names = providers_override
    else:
        provider_names = cfg.get("search", {}).get("providers", ["yts", "apibay", "nyaa"])

    providers: list[SearchProvider] = []
    for name in provider_names:
        prov = create_provider(name, cfg)
        if prov:
            providers.append(prov)

    if not providers_override and "torznab" not in [p.lower() for p in provider_names]:
        if cfg.get("torznab", {}).get("url"):
            prov = create_provider("torznab", cfg)
            if prov:
                providers.append(prov)

    return providers


def unknown_provider_names(names: list[str]) -> list[str]:
    """Return the subset of provider names that are not registered."""
    return [n for n in names if n.lower() not in PROVIDER_REGISTRY]


async def search_all(
    query: str,
    config: dict[str, Any] | None = None,
    providers_override: list[str] | None = None,
    min_seeders: int | None = None,
) -> list[SearchResult]:
    """Search enabled providers concurrently, filter dead torrents, deduplicate, and rank."""
    cfg = config or load_config()
    providers = get_enabled_providers(cfg, providers_override=providers_override)
    logger = get_logger()

    if not providers:
        logger.warning("No search providers enabled")
        return []

    logger.debug("Querying providers: %s for %r", [p.name for p in providers], query)

    tasks = [asyncio.create_task(p.search(query)) for p in providers]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[SearchResult] = []
    for prov, result in zip(providers, raw_results):
        if isinstance(result, Exception):
            logger.warning("Provider %s search failed: %s", prov.name, result)
            continue
        all_items.extend(result)

    if not all_items:
        return []

    # Deduplicate by infohash, keeping the result with highest seeders
    deduped: dict[str, SearchResult] = {}
    for item in all_items:
        key = item.infohash if item.infohash else item.title.lower()
        if key not in deduped:
            deduped[key] = item
        else:
            existing = deduped[key]
            if item.seeders > existing.seeders:
                deduped[key] = item

    merged = list(deduped.values())

    # 1. Filter out dead torrents (0 seeders) if active ones exist
    active_torrents = [r for r in merged if r.seeders > 0]
    if active_torrents:
        merged = active_torrents

    # 2. Optional minimum seeders threshold (CLI override or config)
    raw_threshold = min_seeders if min_seeders is not None else cfg.get("search", {}).get("min_seeders", 0)
    try:
        threshold = int(raw_threshold or 0)
    except (TypeError, ValueError):
        threshold = 0
    if threshold > 0:
        merged = [r for r in merged if r.seeders >= threshold]

    # 3. Relevance and season mismatch checks
    use_fuzzy = cfg.get("search", {}).get("fuzzy", True)

    def sort_key(r: SearchResult) -> tuple[int, int, float]:
        mismatched_season = 1 if _check_season_mismatch(query, r.title) else 0
        f_score = fuzzy_score(query, r.title) if use_fuzzy else 50.0
        # Priority:
        # Tier 0: Matching season/movie (mismatched == 0)
        # Tier 1: Highest seeders
        # Tier 2: Fuzzy title score
        return (-mismatched_season, r.seeders, f_score)

    merged.sort(key=sort_key, reverse=True)

    max_results = cfg.get("search", {}).get("max_results", 40)
    return merged[:max_results]
