# pyright: reportPrivateUsage=false

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from event_helpers import (
    CalendarClock,
    ManualClock,
    offline_client,
    settle,
    wait_count,
    wait_paused,
)
from resource_helpers import Responses, example, first_record

from dankmemer import (
    DailyPolling,
    DisabledPolling,
    Drop,
    EventConfig,
    EventDelivery,
    EventPayloadError,
    FishingEvent,
    PollingConfig,
    PollingResource,
    StoreDailyGift,
    StoreSale,
)
from dankmemer._event_codecs import (
    drops_codec,
    fishing_events_codec,
    gifts_codec,
    sales_codec,
    trending_codec,
)
from dankmemer._live_events import (
    TrendingGameState,
    drop_changes,
    fishing_event_changes,
    gift_changes,
    gifts_allowed,
    sale_changes,
    trending_changes,
)
from dankmemer._parsing import Record
from dankmemer.models.live import (
    parse_drop,
    parse_fishing_event,
    parse_store_daily_gift,
    parse_store_sale,
)
from dankmemer.storage.sqlite import SqliteEventStore


def test_all_live_codecs_preserve_records_collections_nulls_and_unknown_rewards() -> (
    None
):
    drop_data = first_record("/drops")
    drop_data["reward"] = {"type": "future", "extra": {"tags": ["a", "b"]}}
    drop = parse_drop(Record(drop_data, path="/drops"))
    sale = parse_store_sale(Record(first_record("/store-sales"), path="/store-sales"))
    gift = parse_store_daily_gift(
        Record(first_record("/store-daily-gifts"), path="/store-daily-gifts")
    )
    fishing = parse_fishing_event(
        Record(first_record("/fishing-events"), path="/fishing-events")
    )
    drop_codec = drops_codec()
    sale_codec = sales_codec()
    gift_codec = gifts_codec()
    fishing_codec = fishing_events_codec()
    assert drop_codec.decode_snapshot(drop_codec.encode_snapshot((drop,))) == (drop,)
    assert sale_codec.decode_snapshot(sale_codec.encode_snapshot((sale,))) == (sale,)
    assert gift_codec.decode_snapshot(gift_codec.encode_snapshot((gift,))) == (gift,)
    assert fishing_codec.decode_snapshot(fishing_codec.encode_snapshot((fishing,))) == (
        fishing,
    )
    for codec, entry in (
        (drop_codec, drop),
        (sale_codec, sale),
        (gift_codec, gift),
        (fishing_codec, fishing),
    ):
        assert codec.decode_args(codec.encode_args((entry, entry))) == (entry, entry)
        assert codec.decode_args(codec.encode_args(((), (entry,)))) == ((), (entry,))
        with pytest.raises(EventPayloadError):
            codec.encode_args(("wrong model",))
    assert drop_codec.decode_snapshot(drop_codec.encode_snapshot(())) == ()
    trend = TrendingGameState(datetime(2026, 10, 2, tzinfo=UTC), "Game")
    stream_codec = trending_codec()
    assert stream_codec.decode_snapshot(stream_codec.encode_snapshot(trend)) == trend
    assert stream_codec.decode_args(stream_codec.encode_args(("Before", "After"))) == (
        "Before",
        "After",
    )


def test_active_set_events_distinguish_add_update_remove_and_preserve_record_identity() -> (
    None
):
    original: Drop = parse_drop(Record(first_record("/drops"), path="/drops"))
    added = replace(original, id=2)
    updated = replace(original, total_stock=50)
    events = drop_changes((original,), (updated, added), False)
    assert [(event.event, event.args) for event in events] == [
        ("drops_changed", ((original,), (updated, added))),
        ("drop_updated", (original, updated)),
        ("drop_started", (added,)),
    ]
    assert [event.event for event in drop_changes((original,), (), False)] == [
        "drops_changed",
        "drop_ended",
    ]
    assert drop_changes((original,), (original,), False) == ()
    sale: StoreSale = parse_store_sale(
        Record(first_record("/store-sales"), path="/store-sales")
    )
    fishing: FishingEvent = parse_fishing_event(
        Record(first_record("/fishing-events"), path="/fishing-events")
    )
    assert [event.event for event in sale_changes(None, (sale,), True)] == [
        "store_sales_changed",
        "store_sale_started",
    ]
    assert [event.event for event in fishing_event_changes((fishing,), (), False)] == [
        "fishing_events_changed",
        "fishing_event_ended",
    ]


def test_daily_pool_identity_includes_day_and_streaming_emits_on_day_rollover() -> None:
    gift: StoreDailyGift = parse_store_daily_gift(
        Record(first_record("/store-daily-gifts"), path="/store-daily-gifts")
    )
    tomorrow = replace(
        gift,
        day=gift.day + timedelta(days=1),
        expires_at=gift.expires_at + timedelta(days=1),
    )
    assert [event.event for event in gift_changes((gift,), (tomorrow,), False)] == [
        "store_daily_gifts_changed",
        "store_daily_gift",
    ]
    changed = replace(gift, name="Updated")
    assert [event.event for event in gift_changes((gift,), (changed,), False)] == [
        "store_daily_gifts_changed",
        "store_daily_gift_updated",
    ]
    assert not gifts_allowed((tomorrow,), (gift,))
    today = TrendingGameState(datetime(2026, 10, 2, tzinfo=UTC), "Same game")
    following = replace(today, day=today.day + timedelta(days=1))
    assert trending_changes(today, today, False) == ()
    assert trending_changes(today, following, False)[0].event == "stream_trending_game"
    assert trending_changes(today, replace(today, game="New game"), False)[0].args == (
        "Same game",
        "New game",
    )


@pytest.mark.asyncio
async def test_drop_demand_shares_one_worker_ignores_reordering_and_recovers_bad_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = first_record("/drops")
    second = {**first, "id": 2}
    responses = Responses(
        {"data": [first, second]},
        {"data": [second, first]},
        {"data": [{**first, "totalStock": True}]},
        {"data": [{**first, "totalStock": 50}]},
    )
    clock = ManualClock()
    client = offline_client(monkeypatch, responses, clock=clock)
    seen: list[str] = []

    async def updated(before: Drop, after: Drop) -> None:
        seen.append("updated")

    async def ended(drop: Drop) -> None:
        seen.append("ended")

    client.add_listener(updated, name="drop_updated")
    client.add_listener(ended, name="drop_ended")
    async with client:
        await settle()
        assert client.active_polling_resources == frozenset({PollingResource.DROPS})
        clock.advance(59)
        await settle()
        assert len(responses.calls) == 1
        clock.advance(1)
        await settle()
        assert not seen
        clock.advance(60)
        await settle()
        assert PollingResource.DROPS in client.last_poll_errors
        clock.advance(60)
        await settle()
        assert sorted(seen) == ["ended", "updated"]
        assert not client.last_poll_errors
        assert len(responses.calls) == 4 and all(
            call.automatic for call in responses.calls
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "event", "resource"),
    [
        ("/drops", "drop_started", PollingResource.DROPS),
        ("/store-sales", "store_sale_started", PollingResource.STORE_SALES),
        ("/store-daily-gifts", "store_daily_gift", PollingResource.STORE_DAILY_GIFTS),
        ("/fishing-events", "fishing_event_started", PollingResource.FISHING_EVENTS),
        (
            "/stream-trending-game",
            "stream_trending_game",
            PollingResource.STREAM_TRENDING_GAME,
        ),
    ],
)
async def test_every_new_adapter_replays_saved_callback_without_another_api_request(
    route: str,
    event: str,
    resource: PollingResource,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async with await SqliteEventStore.open(str(tmp_path / "events.sqlite")) as store:
        responses = Responses(example(route))
        first = offline_client(
            monkeypatch,
            responses,
            events=EventConfig(delivery=EventDelivery.DURABLE, emit_initial=True),
            event_store=store,
        )

        async def fail(value: object) -> None:
            raise RuntimeError("retry later")

        first.add_listener(fail, name=event, subscription_id="consumer")
        async with first:
            await wait_paused(first, "consumer")
            assert await first.count_pending_events() == 1
            assert (await store.read_checkpoint(resource)) is not None
        before_count = len(responses.calls)
        second = offline_client(
            monkeypatch,
            responses,
            events=EventConfig(delivery=EventDelivery.DURABLE),
            polling=PollingConfig(
                drops=DisabledPolling(),
                store_sales=DisabledPolling(),
                store_daily_gifts=DisabledPolling(),
                fishing_events=DisabledPolling(),
                stream_trending_game=DisabledPolling(),
            ),
            event_store=store,
        )
        received: list[object] = []

        async def succeeds(value: object) -> None:
            received.append(value)

        second.add_listener(succeeds, name=event, subscription_id="consumer")
        async with second:
            await wait_count(store, 0)
            assert len(received) == 1
            assert await store.count_pending_callbacks() == 0
            assert len(responses.calls) == before_count


@pytest.mark.asyncio
async def test_stream_daily_schedule_handles_identical_game_on_next_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = CalendarClock(datetime(2026, 10, 2, 23, 59, tzinfo=UTC))
    responses = Responses(
        example("/stream-trending-game"), example("/stream-trending-game")
    )
    client = offline_client(
        monkeypatch,
        responses,
        clock=clock,
        polling=PollingConfig(stream_trending_game=DailyPolling(offset_seconds=15)),
    )
    received: list[str] = []

    async def game(value: str) -> None:
        received.append(value)

    client.add_listener(game, name="stream_trending_game")
    async with client:
        await settle()
        clock.advance(74)
        await settle()
        assert len(responses.calls) == 1
        clock.advance(1)
        await settle()
        assert received == ["Example game"]
