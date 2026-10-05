import pytest
from tream.utils.magnet import build_magnet, normalize_infohash, parse_magnet


def test_normalize_infohash_hex():
    hex_40 = "E36780C888D30E52516FA8A4F02FA5AC143CEBB2"
    assert normalize_infohash(hex_40) == hex_40.lower()


def test_normalize_infohash_base32():
    # 32-character Base32 representation of 20 bytes
    b32 = "4NTYBSEI2MFVEULPVKS7AL5LVRCDZ2VS"
    normalized = normalize_infohash(b32)
    assert len(normalized) == 40
    assert all(c in "0123456789abcdef" for c in normalized)


def test_normalize_infohash_invalid():
    with pytest.raises(ValueError):
        normalize_infohash("not_a_valid_hash")
    with pytest.raises(ValueError):
        normalize_infohash("12345")


def test_parse_magnet_full():
    uri = (
        "magnet:?xt=urn:btih:e36780c888d30e52516fa8a4f02fa5ac143cebb2"
        "&dn=Ubuntu+22.04+Desktop"
        "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
        "&tr=http%3A%2F%2Ftracker.openbittorrent.com%3A80%2Fannounce"
        "&xl=3654957056"
    )
    info = parse_magnet(uri)
    assert info.infohash == "e36780c888d30e52516fa8a4f02fa5ac143cebb2"
    assert info.name == "Ubuntu 22.04 Desktop"
    assert len(info.trackers) == 2
    assert "udp://tracker.opentrackr.org:1337/announce" in info.trackers
    assert info.size == 3654957056


def test_parse_magnet_raw_hash():
    raw_hash = "e36780c888d30e52516fa8a4f02fa5ac143cebb2"
    info = parse_magnet(raw_hash)
    assert info.infohash == raw_hash
    assert info.name == raw_hash
    assert len(info.trackers) > 0


def test_parse_magnet_invalid():
    with pytest.raises(ValueError):
        parse_magnet("")
    with pytest.raises(ValueError):
        parse_magnet("http://example.com/torrent.torrent")
    with pytest.raises(ValueError):
        parse_magnet("magnet:?dn=TestWithoutHash")


def test_build_magnet():
    h = "e36780c888d30e52516fa8a4f02fa5ac143cebb2"
    mag = build_magnet(h, name="Test Movie", trackers=["udp://tracker.example.com:1337/announce"])
    assert "xt=urn%3Abtih%3A" + h in mag
    assert "dn=Test+Movie" in mag
    assert "tr=udp%3A%2F%2Ftracker.example.com%3A1337%2Fannounce" in mag
