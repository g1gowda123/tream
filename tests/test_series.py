import pytest
from tream.core.series import group_and_sort_episodes, parse_episode


@pytest.mark.parametrize(
    "filename,expected_season,expected_episode",
    [
        ("The.Bear.S02E01.1080p.WEB-DL.mkv", 2, 1),
        ("breaking.bad.s05e14.ozymandias.mp4", 5, 14),
        ("Game of Thrones - 1x01 - Winter Is Coming.mkv", 1, 1),
        ("Severance 01x09.mkv", 1, 9),
        ("True Detective Season 1 Episode 4.mkv", 1, 4),
        ("Chernobyl Season 1 Ep 03.mkv", 1, 3),
        ("Arcane Episode 6.mp4", 1, 6),
        ("Frieren - Ep.08.mkv", 1, 8),
        ("Attack on Titan E15.mkv", 1, 15),
        ("[SubsPlease] Jujutsu Kaisen - 01 (1080p) [9A1B2C3D].mkv", 1, 1),
        ("[Erai-raws] Chainsaw Man - 12 [1080p][Multiple Subtitle].mkv", 1, 12),
    ],
)
def test_parse_episode_patterns(filename: str, expected_season: int, expected_episode: int):
    ep = parse_episode(filename, file_id=1, size=1048576)
    assert ep is not None
    assert ep.season == expected_season
    assert ep.episode == expected_episode
    assert ep.file_id == 1


def test_parse_episode_non_video():
    assert parse_episode("subs.srt") is None
    assert parse_episode("poster.jpg") is None
    assert parse_episode("sample.nfo") is None
    assert parse_episode("instructions.txt") is None


def test_group_and_sort_episodes():
    file_stats = [
        {"id": 1, "path": "The.Show.S01E03.1080p.mkv", "length": 1000},
        {"id": 2, "path": "The.Show.S01E01.1080p.mkv", "length": 1000},
        {"id": 3, "path": "The.Show.S02E02.1080p.mkv", "length": 1000},
        {"id": 4, "path": "The.Show.S01E02.1080p.mkv", "length": 1000},
        {"id": 5, "path": "The.Show.S02E01.1080p.mkv", "length": 1000},
        {"id": 6, "path": "sample.nfo", "length": 100},
        {"id": 7, "path": "subs/english.srt", "length": 50},
    ]

    seasons = group_and_sort_episodes(file_stats)

    assert 1 in seasons
    assert 2 in seasons
    assert len(seasons[1]) == 3
    assert len(seasons[2]) == 2

    # Verify sort order
    assert [e.episode for e in seasons[1]] == [1, 2, 3]
    assert [e.episode for e in seasons[2]] == [1, 2]

    # Verify file IDs
    assert seasons[1][0].file_id == 2  # S01E01
    assert seasons[1][1].file_id == 4  # S01E02
    assert seasons[1][2].file_id == 1  # S01E03
