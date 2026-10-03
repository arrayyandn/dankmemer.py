from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast

import pytest
from event_helpers import offline_client
from resource_helpers import Responses, example, first_record

from dankmemer import Command, DankMemer, Page, TTLCache
from dankmemer.resources import Commands


def documented_calls(client: DankMemer) -> dict[str, Callable[[], Awaitable[object]]]:
    return {
        "/items": client.items.fetch,
        "/pets": client.pets.fetch,
        "/skins": client.skins.fetch,
        "/commands": client.commands.fetch,
        "/fish?category=creatures": client.fishing.creatures.fetch,
        "/fish?category=locations": client.fishing.locations.fetch,
        "/fish?category=npcs": client.fishing.npcs.fetch,
        "/fish?category=tools": client.fishing.tools.fetch,
        "/fish?category=baits": client.fishing.baits.fetch,
        "/fish?category=buckets": client.fishing.buckets.fetch,
        "/fish?category=skills": client.fishing.skills.fetch,
        "/blogs": client.blogs.fetch,
        "/changelogs": client.changelogs.fetch,
        "/global-boosts": client.global_boosts.active,
        "/lottery": client.lottery.latest,
        "/merchant-trades": client.merchant_trades.today,
        "/stream-trending-game": client.stream.trending_game,
        "/drops": client.drops.active,
        "/store-sales": client.store_sales.active,
        "/store-daily-gifts": client.store_daily_gifts.today,
        "/fishing-events": client.fishing_events.active,
        "/users/{user_id}/ban-status": lambda: client.users.is_banned(
            123456789012345678
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(documented_calls(DankMemer("test-token"))))
async def test_public_methods_accept_every_published_response(
    route: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(example(route))
    client = offline_client(monkeypatch, responses)
    try:
        result = await documented_calls(client)[route]()
        if isinstance(result, Page):
            values = cast(Page[object], result)
            assert len(values.items) == 1 and values.next_cursor is None
        elif isinstance(result, tuple):
            assert len(cast(tuple[object, ...], result)) == 1
        elif route == "/stream-trending-game":
            assert result == "Example game"
        elif route == "/users/{user_id}/ban-status":
            assert result is True
        else:
            assert result is not None
        assert len(responses.calls) == 1
        assert responses.calls[0].route.template == route.partition("?")[0]
        assert not responses.calls[0].automatic
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_commands_without_ids_keep_name_lookup_pagination_and_id_index() -> None:
    first = first_record("/commands")
    del first["id"]
    second = {**first, "name": "second"}
    payload = {"data": [first, second], "nextCursor": None}
    commands = Commands(Responses(payload), cache=TTLCache(ttl=timedelta(minutes=1)))
    assert (await commands.get("example")) is not None
    assert await commands.get_by_id("123456789012345678") is None
    page = await commands.fetch()
    assert [entry.id for entry in page.items] == [None, None]
    # An ID appearing between pages must not cause a command to be yielded twice.
    responses = Responses(
        {"data": [first], "nextCursor": 1},
        {"data": [{**first, "id": "loaded"}, second], "nextCursor": None},
    )
    commands = Commands(responses)
    values: list[Command] = [
        entry async for entry in commands.iter(page_size=2, max_limit=None)
    ]
    assert [entry.name for entry in values] == ["example", "second"]
