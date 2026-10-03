# pyright: reportPrivateUsage=false

import asyncio
import gc
from datetime import timedelta

import aiohttp
import pytest
from resource_helpers import ManualClock, Responses, item, page

from dankmemer import (
    CacheConfig,
    DankMemer,
    DankMemerResponseError,
    FishingCacheConfig,
    Item,
    LifecycleError,
    NoCache,
    PaginationError,
    TTLCache,
)
from dankmemer._cache import Cache
from dankmemer.http._routes import Route
from dankmemer.resources import Items


class BlockingResponses(Responses):
    def __init__(self, *payloads: object) -> None:
        super().__init__(*payloads)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.started = 0
        self.cancelled = False

    async def __call__(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        self.started += 1
        payload = await super().__call__(
            route,
            automatic=automatic,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
            category=category,
        )
        if self.started == 1:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return payload


@pytest.mark.asyncio
async def test_complete_catalog_is_one_entry_and_shared_by_every_lookup() -> None:
    records = [
        item(record_id, f"Item {record_id}", key=f"key-{record_id}")
        for record_id in range(163)
    ]
    records[162]["name"] = "Bank Note"
    responses = Responses(
        page(*records[:100], next_cursor=100),
        page(*records[100:]),
        page(item(162, "Bank Note", key="key-162")),
    )
    clock = ManualClock()
    resource = Items(
        responses, cache=TTLCache(ttl=timedelta(seconds=60), max_entries=1), clock=clock
    )
    found = await resource.get("  bANK nOTE  ")
    assert found is not None and found.id == 162
    assert await resource.get_by_id(162) is found
    assert await resource.get_by_key("key-162") is found
    assert await resource.get_many_by_id(162, 999, 162) == (found, None, found)
    assert await resource.search("bank", max_limit=1) == (found,)
    assert await resource.filter(name="BANK NOTE", max_limit=None) == (found,)
    assert len([entry async for entry in resource.iter(max_limit=None)]) == 163
    final_page = await resource.fetch(page_size=2, cursor=162)
    assert final_page.items == (found,) and final_page.next_cursor is None
    assert len(responses.calls) == 2
    clock.elapsed = 59
    assert await resource.get("Bank Note") is found
    clock.elapsed = 60
    assert await resource.get("Bank Note") is not found
    assert [(call.cursor, call.limit) for call in responses.calls] == [
        (0, 100),
        (100, 100),
        (0, 100),
    ]


@pytest.mark.asyncio
async def test_ttl_begins_after_successful_load_and_is_not_sliding() -> None:
    clock = ManualClock()
    responses = Responses(page(item()), page(item()))

    async def delayed(
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        clock.elapsed += 20
        return await responses(
            route,
            automatic=automatic,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
            category=category,
        )

    resource = Items(delayed, cache=TTLCache(ttl=timedelta(seconds=10)), clock=clock)
    found = await resource.get("Test item")
    clock.elapsed = 29
    assert await resource.get("Test item") is found
    assert len(responses.calls) == 1
    clock.elapsed = 30
    await resource.get("Test item")
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_page_cache_uses_lru_entries_without_truncating_records() -> None:
    responses = Responses(page(item(0)), page(item(1)), page(item(2)), page(item(1)))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5), max_entries=2))
    first = await resource.fetch(page_size=1)
    await resource.fetch(page_size=1, cursor=1)
    assert await resource.fetch(page_size=1) is first
    await resource.fetch(page_size=1, cursor=2)
    assert await resource.fetch(page_size=1) is first
    await resource.fetch(page_size=1, cursor=1)
    assert [call.cursor for call in responses.calls] == [0, 1, 2, 1]


@pytest.mark.asyncio
async def test_expired_recently_used_pages_do_not_evict_fresh_pages() -> None:
    responses = Responses(page(item(0)), page(item(1)), page(item(2)), page(item(1)))
    clock = ManualClock()
    resource = Items(
        responses, cache=TTLCache(ttl=timedelta(seconds=10), max_entries=2), clock=clock
    )
    await resource.fetch(page_size=1)
    clock.elapsed = 5
    fresh = await resource.fetch(page_size=1, cursor=1)
    clock.elapsed = 9
    await resource.fetch(page_size=1)
    clock.elapsed = 11
    await resource.fetch(page_size=1, cursor=2)
    assert await resource.fetch(page_size=1, cursor=1) is fresh
    assert len(responses.calls) == 3


@pytest.mark.asyncio
async def test_catalog_reload_does_not_mix_old_cached_pages() -> None:
    responses = Responses(page(item(12, "Old name")), page(item(12, "New name")))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    await resource.fetch()
    assert await resource.get("Old name") is None
    found = await resource.get("New name")
    assert found is not None
    assert (await resource.fetch()).items == (found,)
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_forced_page_refresh_invalidates_a_complete_catalog() -> None:
    responses = Responses(
        page(item(12, "Old name")),
        page(item(12, "New name")),
        page(item(12, "Final name")),
    )
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    await resource.get("Old name")
    assert (await resource.fetch(refresh=True)).items[0].name == "New name"
    assert await resource.get("New name") is None
    assert (await resource.get("Final name")) is not None
    assert len(responses.calls) == 3


@pytest.mark.asyncio
async def test_partial_or_failed_catalog_load_never_reports_a_false_miss() -> None:
    responses = Responses(
        page(item(1), next_cursor=100),
        RuntimeError("failed second page"),
        page(item(2, "Target")),
    )
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    with pytest.raises(RuntimeError, match="failed second page"):
        await resource.get("Target")
    found = await resource.get("Target")
    assert found is not None and found.id == 2
    assert [call.cursor for call in responses.calls] == [0, 100, 0]


@pytest.mark.asyncio
async def test_failed_refresh_preserves_fresh_data_but_never_serves_expired_data() -> (
    None
):
    responses = Responses(
        page(item()),
        page(item(1), next_cursor=100),
        page(item(1)),
        RuntimeError("expired reload failed"),
        page(item(12, "New name")),
    )
    clock = ManualClock()
    resource = Items(responses, cache=TTLCache(ttl=timedelta(seconds=60)), clock=clock)
    found = await resource.get("Test item")
    with pytest.raises(PaginationError, match="overlapping"):
        await resource.get("Test item", refresh=True)
    assert await resource.get("Test item") is found
    clock.elapsed = 60
    with pytest.raises(RuntimeError, match="expired reload failed"):
        await resource.get("Test item")
    assert await resource.get("Test item") is None
    assert len(responses.calls) == 5


@pytest.mark.asyncio
async def test_parse_failures_are_not_cached() -> None:
    invalid = item()
    invalid["marketValue"] = "not an integer"
    responses = Responses(page(invalid), page(item()))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    with pytest.raises(DankMemerResponseError):
        await resource.get("Test item")
    assert await resource.get("Test item") is not None
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_concurrent_lookup_search_and_filter_share_a_load_despite_cancellation() -> (
    None
):
    responses = BlockingResponses(page(item()))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    cancelled = asyncio.create_task(resource.get("Test item"))
    await responses.entered.wait()
    by_id = asyncio.create_task(resource.get_by_id(12))
    search = asyncio.create_task(resource.search("test"))
    filtered = asyncio.create_task(resource.filter(tags=("test",)))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not responses.cancelled and responses.started == 1
    responses.release.set()
    found, matches, selected = await asyncio.gather(by_id, search, filtered)
    assert found is not None and matches == selected == (found,)
    assert await resource.get("Test item") is found
    assert responses.started == 1


@pytest.mark.asyncio
async def test_normal_reads_join_an_in_progress_forced_refresh() -> None:
    initial = Responses(page(item(12, "Old")))
    resource = Items(initial, cache=TTLCache(ttl=timedelta(minutes=5)))
    await resource.get("Old")
    responses = BlockingResponses(page(item(12, "New")))
    resource._request = responses
    refreshing = asyncio.create_task(resource.get("New", refresh=True))
    await responses.entered.wait()
    reading = asyncio.create_task(resource.get_by_id(12))
    await asyncio.sleep(0)
    assert not reading.done()
    responses.release.set()
    found, read = await asyncio.gather(refreshing, reading)
    assert found is not None and read is found
    assert responses.started == 1


@pytest.mark.asyncio
async def test_clear_detaches_an_old_loader_so_it_cannot_overwrite_new_data() -> None:
    responses = BlockingResponses(page(item(12, "Old")), page(item(12, "New")))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(minutes=5)))
    old = asyncio.create_task(resource.get("Old"))
    await responses.entered.wait()
    resource.clear_cache()
    new = await resource.get("New")
    assert new is not None
    responses.release.set()
    previous = await old
    assert previous is not None and previous.name == "Old"
    assert await resource.get("New") is new
    assert await resource.get("Old") is None
    assert responses.started == 2


@pytest.mark.asyncio
async def test_no_cache_retains_nothing_but_coalesces_concurrent_calls() -> None:
    responses = BlockingResponses(page(item()), page(item()))
    resource = Items(responses, cache=NoCache())
    first = asyncio.create_task(resource.get("Test item"))
    await responses.entered.wait()
    second = asyncio.create_task(resource.search("test"))
    await asyncio.sleep(0)
    responses.release.set()
    found, matches = await asyncio.gather(first, second)
    assert matches == (found,) and responses.started == 1
    await resource.get("Test item")
    assert responses.started == 2


@pytest.mark.asyncio
async def test_client_close_cancels_detached_loaders_and_keeps_borrowed_session_open() -> (
    None
):
    async with aiohttp.ClientSession() as session:
        client = DankMemer("test-token", session=session)
        await client.start()
        responses = BlockingResponses(page(item()))
        client.items._request = responses
        pending = asyncio.create_task(client.items.get("Test item"))
        await responses.entered.wait()
        client.clear_cache()
        await client.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert responses.cancelled and not session.closed
        assert not client.items._cache._tasks
        with pytest.raises(LifecycleError):
            await client.items.get("Test item")


@pytest.mark.asyncio
async def test_cache_hits_still_enforce_client_lifecycle_and_event_loop() -> None:
    client = DankMemer("test-token")
    responses = Responses(page(item()))
    client.items._request = responses
    assert await client.items.get("Test item") is not None

    async def other_loop() -> None:
        with pytest.raises(LifecycleError, match="different event loop"):
            await client.items.get("Test item")

    await asyncio.to_thread(lambda: asyncio.run(other_loop()))
    await client.close()
    with pytest.raises(LifecycleError, match="closing or closed"):
        await client.items.get("Test item")
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_client_defaults_overrides_and_cache_clearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    client = DankMemer(
        "test-token", cache=CacheConfig(fishing=FishingCacheConfig(baits=NoCache()))
    )
    monkeypatch.setattr(client._admission.clock, "monotonic", clock.monotonic)
    items = Responses(page(item()), page(item()), page(item()))
    client.items._request = items
    baits = Responses(page(category="baits"), page(category="baits"))
    client.fishing.baits._request = baits
    lottery = Responses({"data": None}, {"data": None})
    client.lottery._request = lottery
    await client.items.get("Test item")
    clock.elapsed = 3599
    await client.items.get("Test item")
    assert len(items.calls) == 1
    clock.elapsed = 3600
    await client.items.get("Test item")
    assert len(items.calls) == 2
    client.clear_cache()
    await client.items.get("Test item")
    assert len(items.calls) == 3
    await client.fishing.baits.get("Missing")
    await client.fishing.baits.get("Missing")
    assert len(baits.calls) == 2
    await client.lottery.latest()
    await client.lottery.latest()
    assert len(lottery.calls) == 2
    await client.close()


@pytest.mark.asyncio
async def test_reference_defaults_expire_after_one_hour_and_fishing_clear_is_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    client = DankMemer("test-token")
    monkeypatch.setattr(client._admission.clock, "monotonic", clock.monotonic)
    pets = Responses(page(), page())
    baits = Responses(
        page(category="baits"), page(category="baits"), page(category="baits")
    )
    client.pets._request = pets
    client.fishing.baits._request = baits
    await client.pets.get("Missing")
    await client.fishing.baits.get("Missing")
    clock.elapsed = 3599
    await client.pets.get("Missing")
    await client.fishing.baits.get("Missing")
    assert len(pets.calls) == len(baits.calls) == 1
    clock.elapsed = 3600
    await client.pets.get("Missing")
    await client.fishing.baits.get("Missing")
    client.fishing.clear_cache()
    await client.pets.get("Missing")
    await client.fishing.baits.get("Missing")
    assert len(pets.calls) == 2 and len(baits.calls) == 3
    await client.close()


@pytest.mark.asyncio
async def test_live_resource_cache_policy_reaches_the_public_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses({"data": []})
    monkeypatch.setattr(DankMemer, "_request_json", responses)
    client = DankMemer(
        "test-token", cache=CacheConfig(drops=TTLCache(ttl=timedelta(minutes=1)))
    )
    try:
        assert await client.drops.active() == ()
        assert await client.drops.get(123) is None
        assert len(responses.calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failed_loader_is_drained_after_its_only_waiter_is_cancelled() -> None:
    cache = Cache(NoCache())
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def failing() -> Item:
        entered.set()
        try:
            await release.wait()
            raise RuntimeError("loader failed")
        finally:
            finished.set()

    failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def record_failure(
        loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        failures.append(context)

    loop.set_exception_handler(record_failure)
    try:
        waiting = asyncio.create_task(cache.get(("catalog", 0, 0), failing))
        await entered.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        release.set()
        await finished.wait()
        await asyncio.sleep(0)
        assert not cache._tasks
        gc.collect()
        assert failures == []
    finally:
        loop.set_exception_handler(previous)
        await cache.close()
