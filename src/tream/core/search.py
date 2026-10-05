import asyncio
from typing import Any
from tream.core.config import load_config
from tream.providers.apibay import ApibayProvider
from tream.providers.base import SearchProvider, SearchResult
from tream.providers.nyaa import NyaaProvider
from tream.providers.torznab import TorznabProvider
from tream.providers.x1337 import X1337Provider
from tream.providers.yts import YTSProvider
from tream.utils.fuzzy import fuzzy_filter, fuzzy_score
from tream.utils.logging import get_logger, status

PROVIDER_REGISTRY: dict[str, type[SearchProvider]] = {
    "yts": YTSProvider,
    "apibay": ApibayProvider,
    "1337x": X1337Provider,
    "nyaa": NyaaProvider,
    "torznab": TorznabProvider,
}


def create_provider(name: str, config: dict[str, Any]) -> SearchProvider | None:
    """Instantiate a search provider by name using configuration settings."""
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


def get_enabled_providers(config: dict[str, Any] | None = None) -> list[SearchProvider]:
    """Retrieve all enabled SearchProvider instances based on config."""
    cfg = config or load_config()
    provider_names = cfg.get("search", {}).get("providers", ["yts", "apibay", "nyaa"])

    providers: list[SearchProvider] = []
    for name in provider_names:
        prov = create_provider(name, cfg)
        if prov:
            providers.append(prov)

    # Also automatically include Torznab if URL configured and not in list
    if "torznab" not in [p.lower() for p in provider_names]:
        if cfg.get("torznab", {}).get("url"):
            prov = create_provider("torznab", cfg)
            if prov:
                providers.append(prov)

    return providers


async def search_all(query: str, config: dict[str, Any] | None = None) -> list[SearchResult]:
    """Search enabled providers concurrently, deduplicate by infohash, and sort."""
    cfg = config or load_config()
    providers = get_enabled_providers(cfg)
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

    # Fuzzy matching fallback if fuzzy enabled and exact matches are sparse
    use_fuzzy = cfg.get("search", {}).get("fuzzy", True)
    q_lower = query.lower().strip()

    if use_fuzzy:
        # Check if there is an exact match in the titles
        has_direct_match = any(q_lower in r.title.lower() for r in merged)
        if not has_direct_match:
            scored = fuzzy_filter(query, merged, key=lambda r: r.title, score_cutoff=40.0)
            if scored:
                # Reorder based on combined fuzzy score and seeder count
                merged = [
                    pair[0]
                    for pair in sorted(
                        scored,
                        key=lambda pair: (pair[1], pair[0].seeders),
                        reverse=True,
                    )
                ]

    # Sort primarily by seeders descending
    merged.sort(key=lambda r: r.seeders, reverse=True)

    max_results = cfg.get("search", {}).get("max_results", 20)
    return merged[:max_results]
