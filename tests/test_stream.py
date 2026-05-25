from datetime import datetime, timezone

import pytest

from dankmemer import DankMemerClient, StreamData, TrendingGame


stream_payload = {
    "trending_game": {
        "lastModified": "2026-05-25T18:18:07.690810+00:00",
        "name": "Among Us",
    }
}


@pytest.mark.asyncio
async def test_stream_query_returns_stream_data_and_uses_cache():
    requested_routes = []

    async def dummy_request(route: str, params: dict = None):
        requested_routes.append(route)
        return stream_payload

    async with DankMemerClient(cache_ttl_hours=1) as client:
        client.request = dummy_request

        first = await client.stream.query()
        second = await client.stream.query()

    assert isinstance(first, StreamData)
    assert isinstance(first.trending_game, TrendingGame)
    assert first.trending_game.name == "Among Us"
    assert first.trending_game.lastModified == datetime(
        2026, 5, 25, 18, 18, 7, 690810, tzinfo=timezone.utc
    )
    assert first is second
    assert requested_routes == ["stream"]


def test_stream_top_level_imports_work():
    from dankmemer import StreamData as ImportedStreamData
    from dankmemer import TrendingGame as ImportedTrendingGame

    assert ImportedStreamData is StreamData
    assert ImportedTrendingGame is TrendingGame
