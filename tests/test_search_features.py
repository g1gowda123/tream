from tream.core.search import _check_season_mismatch
from tream.providers.base import SearchResult
from tream.utils.picker import _format_result_label


def test_season_mismatch_detection():
    # Query asks for season 1
    assert _check_season_mismatch("jujutsu kaisen season 1", "Jujutsu Kaisen 2nd Season - 07") is True
    assert _check_season_mismatch("jujutsu kaisen season 1", "Jujutsu Kaisen S02E05") is True
    assert _check_season_mismatch("jujutsu kaisen season 1", "Jujutsu Kaisen Season 1 Multi") is False
    assert _check_season_mismatch("jujutsu kaisen season 1", "Jujutsu Kaisen (Season 1 + 2 + Movie)") is False

    # Query with no season specification
    assert _check_season_mismatch("blade runner", "Blade Runner 2049") is False


def test_format_result_label():
    res = SearchResult(
        title="Test Movie",
        magnet="magnet:?xt=urn:btih:e36780c888d30e52516fa8a4f02fa5ac143cebb2",
        infohash="e36780c888d30e52516fa8a4f02fa5ac143cebb2",
        size=1073741824,
        seeders=45,
        leechers=3,
        provider="yts",
        quality="1080P",
        category="Movies",
    )
    label = _format_result_label(res, 1)
    assert "1." in label
    assert "[yts]" in label
    assert "Test Movie" in label
    assert "1.0 GB" in label
    assert "S:45 L:3" in label
    assert "[1080P]" in label
