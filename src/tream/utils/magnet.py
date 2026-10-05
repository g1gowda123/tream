import base64
from dataclasses import dataclass, field
import re
import urllib.parse

DEFAULT_TRACKERS: list[str] = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://public.popcorn-tracker.org:6969/announce",
    "udp://explodie.org:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
]

_HEX_40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_B32_32_RE = re.compile(r"^[2-7a-zA-Z]{32}$")


@dataclass
class MagnetInfo:
    infohash: str
    name: str = ""
    trackers: list[str] = field(default_factory=list)
    size: int | None = None
    raw_uri: str = ""


def normalize_infohash(hash_val: str) -> str:
    """Normalize 40-char hex or 32-char base32 infohash to lowercase 40-char hex."""
    clean = hash_val.strip()
    if _HEX_40_RE.match(clean):
        return clean.lower()

    if _B32_32_RE.match(clean):
        try:
            raw_bytes = base64.b32decode(clean.upper())
            return raw_bytes.hex().lower()
        except Exception as err:
            raise ValueError(f"Failed to decode base32 infohash: {hash_val!r}") from err

    raise ValueError(f"Invalid infohash format (expected 40-char hex or 32-char base32): {hash_val!r}")


def parse_magnet(uri_or_hash: str) -> MagnetInfo:
    """Parse magnet URI or raw infohash into structured MagnetInfo."""
    trimmed = uri_or_hash.strip()
    if not trimmed:
        raise ValueError("Empty magnet or hash string")

    # Handle raw hash passed instead of full magnet URI
    if _HEX_40_RE.match(trimmed) or _B32_32_RE.match(trimmed):
        normalized = normalize_infohash(trimmed)
        raw = build_magnet(normalized)
        return MagnetInfo(infohash=normalized, name=normalized, trackers=list(DEFAULT_TRACKERS), raw_uri=raw)

    if not trimmed.startswith("magnet:?"):
        raise ValueError(f"Invalid magnet URI: {uri_or_hash!r}")

    query_string = trimmed[len("magnet:?"):]
    parsed = urllib.parse.parse_qs(query_string, keep_blank_values=True)

    xt_list = parsed.get("xt", [])
    infohash = ""
    for xt in xt_list:
        if xt.lower().startswith("urn:btih:"):
            raw_hash = xt[len("urn:btih:"):]
            infohash = normalize_infohash(raw_hash)
            break

    if not infohash:
        raise ValueError(f"No BTIH infohash found in magnet URI: {uri_or_hash!r}")

    dn = parsed.get("dn", [""])[0]
    name = urllib.parse.unquote_plus(dn) if dn else infohash

    trackers: list[str] = []
    for tr in parsed.get("tr", []):
        decoded_tr = urllib.parse.unquote(tr).strip()
        if decoded_tr and decoded_tr not in trackers:
            trackers.append(decoded_tr)

    size = None
    if "xl" in parsed and parsed["xl"]:
        try:
            size = int(parsed["xl"][0])
        except (ValueError, TypeError):
            size = None

    return MagnetInfo(
        infohash=infohash,
        name=name,
        trackers=trackers,
        size=size,
        raw_uri=trimmed,
    )


def build_magnet(infohash: str, name: str | None = None, trackers: list[str] | None = None) -> str:
    """Construct a standardized magnet URI from infohash, name, and trackers."""
    norm_hash = normalize_infohash(infohash)
    params: list[tuple[str, str]] = [("xt", f"urn:btih:{norm_hash}")]
    if name:
        params.append(("dn", name))

    selected_trackers = trackers if trackers is not None else DEFAULT_TRACKERS
    for tr in selected_trackers:
        if tr:
            params.append(("tr", tr))

    return "magnet:?" + urllib.parse.urlencode(params)
