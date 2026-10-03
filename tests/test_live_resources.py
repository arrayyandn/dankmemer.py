from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch as mock_patch

import pytest
from resource_helpers import ManualClock, Responses, first_record, item, page

from dankmemer import DankMemerResponseError, Drop, StoreDailyGift, TTLCache
from dankmemer._parsing import Record
from dankmemer.models.live import parse_drop, parse_store_daily_gift
from dankmemer.resources import Drops, FishingEvents, Items, StoreDailyGifts, StoreSales


@pytest.mark.asyncio
async def test_drop_filters_lookups_ttl_and_expiry_share_one_snapshot() -> None:
    base = first_record("/drops")
    values = {
        "data": [
            base,
            {**base, "id": 2, "kind": "Exclusive", "patreonOnly": True},
            {**base, "id": 3, "partnerOnly": True},
        ]
    }
    responses = Responses(values, {"data": []})
    clock = ManualClock()
    drops = Drops(responses, cache=TTLCache(ttl=timedelta(minutes=1)), clock=clock)
    assert [
        drop.id for drop in await drops.active(kind=" exclusive ", patreon_only=True)
    ] == [2]
    assert [drop.id for drop in await drops.active(partner_only=True)] == [3]
    assert (await drops.get(1)) is not None
    assert await drops.get(99) is None
    assert len(responses.calls) == 1
    clock.elapsed = 60
    assert await drops.active() == ()
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_sale_subject_and_item_conveniences_use_local_filters() -> None:
    base = first_record("/store-sales")
    responses = Responses(
        {"data": [base, {**base, "id": 2, "subjectKind": "subscription"}]}
    )
    sales = StoreSales(responses, cache=TTLCache(ttl=timedelta(minutes=1)))
    assert len(await sales.active(subject_kind=" ITEM ")) == 1
    assert [sale.id for sale in await sales.for_item(2)] == [1]
    assert await sales.for_item(999) == ()
    assert (await sales.get(2)) is not None
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_daily_gift_item_rewards_and_unknown_metadata_are_preserved() -> None:
    data = first_record("/store-daily-gifts")
    data["reward"] = {
        "type": "item",
        "item": 12,
        "quantity": 2,
        "future": {"tags": ["a"]},
    }
    data["metadata"] = {
        "render": "2 items",
        "imageUrl": "https://example.test/image",
        "future": [1, 2],
    }
    responses = Responses({"data": [data]}, page(item()))
    gifts = StoreDailyGifts(
        responses, items=Items(responses), cache=TTLCache(ttl=timedelta(minutes=1))
    )
    gift = await gifts.get("example")
    assert gift is not None
    assert gift.reward.data["future"] == {"tags": ("a",)}
    assert gift.metadata.data["future"] == (1, 2)
    assert await gifts.today(category=" DAILY ") == (gift,)
    resolved = await gifts.reward_item(gift)
    assert resolved is not None and resolved.name == "Test item"
    with pytest.raises(TypeError):
        cast(dict[str, object], gift.metadata.data)["render"] = "changed"
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_fishing_event_filters_and_id_lookup_share_cached_data() -> None:
    base = first_record("/fishing-events")
    responses = Responses(
        {"data": [base, {**base, "id": "second", "premiumOnly": True}]}
    )
    events = FishingEvents(responses, cache=TTLCache(ttl=timedelta(minutes=1)))
    assert [entry.id for entry in await events.active(premium_only=True)] == ["second"]
    assert len(await events.active(type_id="example")) == 2
    assert await events.get("missing") is None
    assert len(responses.calls) == 1


@pytest.mark.parametrize(
    "route", ["/drops", "/store-sales", "/store-daily-gifts", "/fishing-events"]
)
@pytest.mark.parametrize("patch", [{"id": True}, {"startsAt": "no date"}, {"id": None}])
def test_live_required_fields_reject_invalid_types(
    route: str, patch: dict[str, object]
) -> None:
    from dankmemer.models.live import parse_fishing_event, parse_store_sale

    parsers = {
        "/drops": parse_drop,
        "/store-sales": parse_store_sale,
        "/store-daily-gifts": parse_store_daily_gift,
        "/fishing-events": parse_fishing_event,
    }
    value = first_record(route)
    if route == "/store-daily-gifts" and "startsAt" in patch:
        patch = {"day": "no date"}
    with pytest.raises(DankMemerResponseError):
        parsers[route](Record({**value, **patch}, path=route))


@pytest.mark.asyncio
async def test_duplicate_collection_ids_and_http_errors_never_become_empty_successes() -> (
    None
):
    from dankmemer import NotFound

    value = first_record("/drops")
    responses = Responses(
        {"data": [value, value]}, NotFound("missing", status_code=404, path="/drops")
    )
    drops = Drops(responses, cache=TTLCache(ttl=timedelta(minutes=1)))
    with pytest.raises(DankMemerResponseError, match="duplicate"):
        await drops.active()
    with pytest.raises(NotFound):
        await drops.get(1)


def test_nullable_drop_rewards_and_time_helpers() -> None:
    value = first_record("/drops")
    value["reward"] = None
    value["specialCost"] = 1.5
    value["endsAt"] = "2000-01-01T00:00:00Z"
    drop: Drop = parse_drop(Record(value, path="/drops"))
    assert drop.reward is None and drop.cost is None and drop.kind is None
    assert drop.special_cost == 1.5
    gift: StoreDailyGift = parse_store_daily_gift(
        Record(first_record("/store-daily-gifts"), path="/store-daily-gifts")
    )
    assert gift.day.tzinfo is UTC
    with mock_patch("dankmemer.models.live.datetime") as clock:
        clock.now.return_value = datetime(2001, 1, 1, tzinfo=UTC)
        assert drop.time_remaining == timedelta(0) and not drop.is_active
        for now, active in (
            (gift.day - timedelta(seconds=1), False),
            (gift.day, True),
            (gift.expires_at - timedelta(seconds=1), True),
            (gift.expires_at, False),
        ):
            clock.now.return_value = now
            assert gift.is_active is active
            assert gift.time_remaining == max(timedelta(0), gift.expires_at - now)
