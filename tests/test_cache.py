import pytest
from tream.utils.cache import buffer_to_vlc_caching_ms, format_bytes, parse_buffer_size


def test_parse_buffer_size_standard():
    assert parse_buffer_size("256M") == 256 * 1024 * 1024
    assert parse_buffer_size("512M") == 512 * 1024 * 1024
    assert parse_buffer_size("1G") == 1024 * 1024 * 1024
    assert parse_buffer_size("2G") == 2 * 1024 * 1024 * 1024


def test_parse_buffer_size_variations():
    assert parse_buffer_size("256MB") == 268435456
    assert parse_buffer_size("256mib") == 268435456
    assert parse_buffer_size("512m") == 536870912
    assert parse_buffer_size("1g") == 1073741824
    assert parse_buffer_size("1GB") == 1073741824
    assert parse_buffer_size("1024K") == 1024 * 1024
    assert parse_buffer_size("1048576") == 1048576
    assert parse_buffer_size(1048576) == 1048576
    assert parse_buffer_size(268435456.0) == 268435456


def test_parse_buffer_size_garbage():
    garbage_inputs = [
        "garbage",
        "256XYZ",
        "M",
        "---",
        "",
        "   ",
        "-256M",
        "0M",
        -100,
        0,
    ]
    for inp in garbage_inputs:
        with pytest.raises(ValueError):
            parse_buffer_size(inp)  # type: ignore


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 * 1024) == "1.0 MB"
    assert format_bytes(256 * 1024 * 1024) == "256.0 MB"
    assert format_bytes(1024 * 1024 * 1024) == "1.0 GB"


def test_buffer_to_vlc_caching_ms():
    ms_small = buffer_to_vlc_caching_ms(1024 * 1024)
    assert ms_small >= 5000

    ms_256m = buffer_to_vlc_caching_ms(256 * 1024 * 1024)
    assert 5000 <= ms_256m <= 60000

    ms_large = buffer_to_vlc_caching_ms(10 * 1024 * 1024 * 1024)
    assert ms_large == 60000
