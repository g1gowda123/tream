from typing import Any, Callable, TypeVar
from rapidfuzz import fuzz

T = TypeVar("T")


def fuzzy_score(query: str, candidate: str) -> float:
    """Compute fuzzy match score (0.0 to 100.0) between query and candidate string."""
    q = query.strip().lower()
    c = candidate.strip().lower()
    if not q or not c:
        return 0.0

    # Direct substring matches get an instant high score
    if q in c:
        return 95.0 + (5.0 * (len(q) / max(len(c), 1)))

    token_score = float(fuzz.token_set_ratio(q, c))
    w_score = float(fuzz.WRatio(q, c))
    return max(token_score, w_score)


def fuzzy_filter(
    query: str,
    items: list[T],
    key: Callable[[T], str] | None = None,
    score_cutoff: float = 50.0,
) -> list[tuple[T, float]]:
    """Filter and rank items against a query using fuzzy matching."""
    if not query.strip():
        return [(item, 100.0) for item in items]

    scorer = key if key is not None else lambda x: str(x)
    scored: list[tuple[T, float]] = []

    for item in items:
        text = scorer(item)
        score = fuzzy_score(query, text)
        if score >= score_cutoff:
            scored.append((item, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
