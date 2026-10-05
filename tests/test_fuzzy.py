from tream.providers.base import SearchResult
from tream.utils.fuzzy import fuzzy_filter, fuzzy_score


def test_fuzzy_score_exact_and_substring():
    score_exact = fuzzy_score("blade runner", "blade runner")
    assert score_exact >= 95.0

    score_sub = fuzzy_score("blade", "blade runner 2049")
    assert score_sub >= 95.0


def test_fuzzy_score_typo():
    score_typo = fuzzy_score("bladerunr", "Blade Runner (1982)")
    assert score_typo >= 60.0

    score_unrelated = fuzzy_score("avatar", "The Godfather")
    assert score_unrelated < 50.0
    assert score_typo > score_unrelated + 20.0


def test_fuzzy_filter_fallback():
    candidates = [
        "Blade Runner 2049 (2017) 1080p",
        "Blade Runner Final Cut 1982 720p",
        "The Godfather Part II",
        "Spider-Man No Way Home",
    ]

    # Query with a typo: "bladerunr"
    results = fuzzy_filter("bladerunr", candidates, score_cutoff=50.0)
    matched_texts = [r[0] for r in results]

    assert len(matched_texts) == 2
    assert "Blade Runner 2049 (2017) 1080p" in matched_texts
    assert "Blade Runner Final Cut 1982 720p" in matched_texts
    assert "The Godfather Part II" not in matched_texts


def test_fuzzy_filter_empty_query():
    candidates = ["Movie A", "Movie B"]
    results = fuzzy_filter("", candidates)
    assert len(results) == 2
