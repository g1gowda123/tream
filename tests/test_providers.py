import pytest
import httpx
from tream.core.search import search_all
from tream.providers.apibay import ApibayProvider
from tream.providers.base import SearchResult
from tream.providers.nyaa import NyaaProvider
from tream.providers.yts import YTSProvider


@pytest.mark.asyncio
async def test_yts_provider():
    mock_response = {
        "status": "ok",
        "data": {
            "movie_count": 1,
            "movies": [
                {
                    "title_long": "Blade Runner (1982)",
                    "torrents": [
                        {
                            "hash": "e36780c888d30e52516fa8a4f02fa5ac143cebb2",
                            "quality": "1080p",
                            "type": "bluray",
                            "seeds": 25,
                            "peers": 5,
                            "size_bytes": 2147483648,
                        }
                    ],
                }
            ],
        },
    }

    async def mock_handler(request):
        return httpx.Response(200, json=mock_response)

    transport = httpx.MockTransport(mock_handler)
    provider = YTSProvider()

    # Monkeypatch AsyncClient inside search
    original_client = httpx.AsyncClient

    def custom_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    httpx.AsyncClient = custom_client
    try:
        results = await provider.search("blade runner")
        assert len(results) == 1
        assert "Blade Runner (1982)" in results[0].title
        assert results[0].quality == "1080P"
        assert results[0].seeders == 25
        assert results[0].size == 2147483648
    finally:
        httpx.AsyncClient = original_client


@pytest.mark.asyncio
async def test_apibay_provider():
    mock_items = [
        {
            "id": "12345",
            "name": "Ubuntu 22.04 LTS 64bit",
            "info_hash": "e36780c888d30e52516fa8a4f02fa5ac143cebb2",
            "leechers": "3",
            "seeders": "40",
            "size": "3654957056",
            "category": "300",
        },
        {
            "id": "0",
            "name": "No results returned",
            "info_hash": "0000000000000000000000000000000000000000",
            "leechers": "0",
            "seeders": "0",
            "size": "0",
            "category": "0",
        },
    ]

    async def mock_handler(request):
        return httpx.Response(200, json=mock_items)

    transport = httpx.MockTransport(mock_handler)
    provider = ApibayProvider()

    original_client = httpx.AsyncClient

    def custom_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    httpx.AsyncClient = custom_client
    try:
        results = await provider.search("ubuntu")
        assert len(results) == 1
        assert results[0].title == "Ubuntu 22.04 LTS 64bit"
        assert results[0].seeders == 40
        assert results[0].category == "Applications"
    finally:
        httpx.AsyncClient = original_client


@pytest.mark.asyncio
async def test_nyaa_provider():
    mock_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
      <channel>
        <title>Nyaa</title>
        <item>
          <title>[SubsPlease] Frieren - 28 (1080p) [ABCD1234].mkv</title>
          <nyaa:infoHash>e36780c888d30e52516fa8a4f02fa5ac143cebb2</nyaa:infoHash>
          <nyaa:seeders>120</nyaa:seeders>
          <nyaa:leechers>10</nyaa:leechers>
          <nyaa:size>1.4 GiB</nyaa:size>
          <nyaa:category>Anime - English-translated</nyaa:category>
        </item>
      </channel>
    </rss>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=mock_xml)

    transport = httpx.MockTransport(mock_handler)
    provider = NyaaProvider()

    original_client = httpx.AsyncClient

    def custom_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    httpx.AsyncClient = custom_client
    try:
        results = await provider.search("frieren")
        assert len(results) == 1
        assert "[SubsPlease] Frieren - 28" in results[0].title
        assert results[0].seeders == 120
        assert results[0].quality == "1080P"
        assert results[0].size == int(1.4 * 1024 * 1024 * 1024)
    finally:
        httpx.AsyncClient = original_client
