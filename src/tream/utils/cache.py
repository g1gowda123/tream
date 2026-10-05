import re

UNIT_MULTIPLIERS: dict[str, int] = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024 * 1024,
    "mb": 1024 * 1024,
    "mib": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
    "gb": 1024 * 1024 * 1024,
    "gib": 1024 * 1024 * 1024,
    "t": 1024 * 1024 * 1024 * 1024,
    "tb": 1024 * 1024 * 1024 * 1024,
    "tib": 1024 * 1024 * 1024 * 1024,
}

_BUFFER_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)\s*$")


def parse_buffer_size(val: str | int | float) -> int:
    """Parse human readable buffer string (e.g. 256M, 1G) into bytes."""
    if isinstance(val, (int, float)):
        size_bytes = int(val)
        if size_bytes <= 0:
            raise ValueError(f"Buffer size must be positive, got: {val}")
        return size_bytes

    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Invalid buffer size: {val!r}")

    match = _BUFFER_PATTERN.match(val)
    if not match:
        raise ValueError(f"Invalid buffer size: {val!r}")

    number_str, unit_str = match.groups()
    try:
        number = float(number_str)
    except ValueError as err:
        raise ValueError(f"Invalid buffer size number: {val!r}") from err

    if number <= 0:
        raise ValueError(f"Buffer size must be positive, got: {val}")

    unit = unit_str.lower()
    if not unit:
        return int(number)

    multiplier = UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ValueError(f"Unknown buffer size unit: {unit_str!r} in {val!r}")

    return int(number * multiplier)


def format_bytes(bytes_count: int | float) -> str:
    """Format bytes count into human readable string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(bytes_count)
    unit_idx = 0
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(val)} B"
    return f"{val:.1f} {units[unit_idx]}"


def buffer_to_vlc_caching_ms(buffer_bytes: int) -> int:
    """Map buffer size in bytes to a sensible network caching duration for VLC in ms."""
    # Assuming nominal 15 Mbps video bit rate: 1.875 MB/sec.
    # 256 MB buffer corresponds to ~136s. We cap between 5,000ms and 60,000ms for responsiveness.
    seconds = buffer_bytes / (1.875 * 1024 * 1024)
    ms = int(seconds * 1000)
    return max(5000, min(ms, 60000))
