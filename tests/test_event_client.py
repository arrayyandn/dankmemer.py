# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import assert_type, cast

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from event_helpers import (
    CalendarClock,
    ManualClock,
    boosts,
    lottery,
    offline_client,
    settle,
)
from resource_helpers import Responses, blogs

from dankmemer import (
    Blog,
    CacheConfig,
    DailyPolling,
    DankMemer,
    DisabledPolling,
    EventConfig,
    GlobalBoost,
    HourlyPolling,
    IntervalPolling,
    LotteryResult,
    PollingConfig,
    PollingResource,
    TTLCache,
)
from dankmemer._event_client import calendar_delay
from dankmemer.errors import (
    AuthenticationError,
    BadRequest,
    ConfigurationError,
    DankMemerHTTPError,
    EventRecoveryError,
    Forbidden,
    LifecycleError,
)
from dankmemer.http._admission import Admission
from dankmemer.http._routes import BLOGS, GLOBAL_BOOSTS, LOTTERY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [(BadRequest, 400), (AuthenticationError, 401), (Forbidden, 403)],
)
async def test_http_access_failure_pauses_polling_until_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[DankMemerHTTPError],
    status_code: int,
) -> None:
    error = error_type(
        "request rejected", status_code=status_code, path="/global-boosts"
    )
    responses = Responses(boosts(2), error, boosts(3))
    clock = ManualClock()
    dank = offline_client(monkeypatch, responses, clock=clock)
    received: list[tuple[tuple[GlobalBoost, ...], tuple[GlobalBoost, ...]]] = []

    async def changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        received.append((before, after))

    listener_id = dank.add_listener(changed, name="global_boosts_changed")
    resource = PollingResource.GLOBAL_BOOSTS
    async with dank:
        await settle()
        clock.advance(60)
        await settle()
        assert dank.paused_polling_resources == frozenset({resource})
        assert dank.last_poll_errors[resource] is error
        assert not received

        assert dank.remove_listener(listener_id)
        dank.add_listener(changed, name="global_boosts_changed")
        dank._events.scheduler.set_external_resources(frozenset({resource}))
        clock.advance(600)
        await settle()
        assert len(responses.calls) == 2
        assert dank.active_polling_resources == frozenset({resource})
        assert dank.paused_polling_resources == frozenset({resource})

        dank.retry_polling(resource)
        assert dank.last_poll_errors[resource] is error
        await settle()
        assert len(responses.calls) == 3
        assert not dank.paused_polling_resources
        assert not dank.last_poll_errors
        assert len(received) == 1
        before, after = received[0]
        assert before[0].multiplier == 2
        assert after[0].multiplier == 3


@pytest.mark.asyncio
async def test_explicit_poll_retry_preserves_spacing_and_can_pause_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = Forbidden("permission missing", status_code=403, path="/global-boosts")
    responses = Responses(error, error, boosts(2))
    clock = ManualClock()
    dank = offline_client(monkeypatch, responses, clock=clock)

    @dank.event
    async def on_global_boosts_changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        pass

    resource = PollingResource.GLOBAL_BOOSTS
    async with dank:
        await settle()
        dank.retry_polling(resource)
        dank.retry_polling(resource)
        clock.advance(59)
        await settle()
        assert len(responses.calls) == 1
        assert dank.last_poll_errors[resource] is error
        clock.advance(1)
        await settle()
        assert len(responses.calls) == 2
        assert dank.paused_polling_resources == frozenset({resource})

        dank.retry_polling(resource)
        clock.advance(60)
        await settle()
        assert len(responses.calls) == 3
        assert not dank.paused_polling_resources
        assert not dank.last_poll_errors


@pytest.mark.asyncio
async def test_poll_retry_requires_running_client_and_enabled_resource() -> None:
    dank = DankMemer("test-token", polling=PollingConfig(drops=DisabledPolling()))
    with pytest.raises(LifecycleError):
        dank.retry_polling(PollingResource.LOTTERY)
    async with dank:
        with pytest.raises(ConfigurationError):
            dank.retry_polling(PollingResource.DROPS)
        with pytest.raises(ConfigurationError):
            dank.retry_polling(cast(PollingResource, "lottery"))
        dank.retry_polling(PollingResource.LOTTERY)
        assert not dank.paused_polling_resources
    with pytest.raises(LifecycleError):
        dank.retry_polling(PollingResource.LOTTERY)


@pytest.mark.asyncio
async def test_client_polls_only_requested_resources_and_shares_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(boosts(2), boosts(3))
    clock = ManualClock()
    client = offline_client(monkeypatch, responses, clock=clock)
    received: asyncio.Queue[tuple[tuple[GlobalBoost, ...], tuple[GlobalBoost, ...]]] = (
        asyncio.Queue()
    )

    async def changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await received.put((before, after))

    async def second(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        return None

    await client.start()
    try:
        await settle()
        assert not responses.calls
        assert client.active_polling_resources == frozenset()
        first_id = client.add_listener(changed, name="global_boosts_changed")
        second_id = client.add_listener(second, name="on_global_boosts_changed")
        assert client.add_listener(changed, name="global_boosts_changed") == first_id
        await settle()
        assert [call.route for call in responses.calls] == [GLOBAL_BOOSTS]
        assert all(call.automatic for call in responses.calls)
        assert received.empty()
        assert client.active_polling_resources == frozenset(
            {PollingResource.GLOBAL_BOOSTS}
        )
        clock.advance(59)
        await settle()
        assert len(responses.calls) == 1
        clock.advance(1)
        before, after = await asyncio.wait_for(received.get(), timeout=1)
        assert before[0].multiplier == 2
        assert after[0].multiplier == 3
        assert len(responses.calls) == 2
        assert client.remove_listener(first_id)
        assert client.remove_listener(second_id)
        await settle()
        clock.advance(120)
        await settle()
        assert len(responses.calls) == 2
        assert not client.active_polling_resources
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_primary_event_replaces_only_primary_handler_and_preserves_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = offline_client(
        monkeypatch, Responses(lottery(1)), events=EventConfig(emit_initial=True)
    )
    seen: list[str] = []

    async def old(result: LotteryResult) -> None:
        seen.append("old")

    async def current(result: LotteryResult) -> None:
        seen.append("current")

    async def additional(result: LotteryResult) -> None:
        seen.append("additional")

    old.__name__ = "on_lottery_result"
    current.__name__ = "on_lottery_result"
    old_typed = cast(Callable[[LotteryResult], Awaitable[None]], old)
    current_typed = cast(Callable[[LotteryResult], Awaitable[None]], current)
    additional_typed = cast(Callable[[LotteryResult], Awaitable[None]], additional)
    assert_type(client.event(old_typed), Callable[[LotteryResult], Awaitable[None]])
    assert_type(client.event(current_typed), Callable[[LotteryResult], Awaitable[None]])
    assert_type(
        client.listen("lottery_result")(additional_typed),
        Callable[[LotteryResult], Awaitable[None]],
    )
    async with client:
        await settle()
        assert sorted(seen) == ["additional", "current"]
        assert (await client.count_pending_events()) == 0


@pytest.mark.asyncio
async def test_decorated_callbacks_can_be_removed_by_name_without_a_listener_id() -> (
    None
):
    client = DankMemer("test-token")

    @client.event
    async def on_lottery_result(result: LotteryResult) -> None:
        return None

    try:
        assert client.active_polling_resources == frozenset({PollingResource.LOTTERY})
        assert client.remove_listener(on_lottery_result)
        assert not client.remove_listener(on_lottery_result)
        assert not client.active_polling_resources
        assert client._event_handlers == {}
        client.event(on_lottery_result)
        assert client.remove_listener(on_lottery_result, name="lottery_result")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bound_callback_removal_matches_instance_and_requested_event() -> None:
    class Handler:
        async def changed(
            self, before: tuple[object, ...], after: tuple[object, ...]
        ) -> None:
            return None

    first, second = Handler(), Handler()
    client = DankMemer("test-token")
    try:
        client.listen("global_boosts_changed")(first.changed)
        second_id = client.add_listener(second.changed, name="global_boosts_changed")
        client.add_listener(first.changed, name="drops_changed")
        assert client.remove_listener(first.changed, name="on_global_boosts_changed")
        assert not client.remove_listener(first.changed, name="global_boosts_changed")
        assert client.active_polling_resources == frozenset(
            {PollingResource.GLOBAL_BOOSTS, PollingResource.DROPS}
        )
        assert client.remove_listener(second_id)
        assert client.active_polling_resources == frozenset({PollingResource.DROPS})
        assert client.remove_listener(first.changed, name="drops_changed")
        assert not client.active_polling_resources
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_removal_rejects_invalid_ids_names_and_synchronous_callbacks() -> None:
    client = DankMemer("test-token")
    operation = cast(Callable[..., bool], client.remove_listener)

    def callback() -> None:
        return None

    try:
        for value in (0, -1, True, None, "1"):
            with pytest.raises(ConfigurationError):
                operation(value)
        with pytest.raises(ConfigurationError):
            operation(1, name="drop_started")
        with pytest.raises(ConfigurationError):
            operation(callback, name="drop_started")
        with pytest.raises(ConfigurationError):
            operation(callback, name="unknown")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reordering_boosts_does_not_emit_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(boosts(2, 3), boosts(3, 2))
    clock = ManualClock()
    client = offline_client(monkeypatch, responses, clock=clock)
    seen: list[tuple[GlobalBoost, ...]] = []

    @client.event
    async def on_global_boosts_changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        seen.append(after)

    async with client:
        await settle()
        clock.advance(60)
        await settle()
        assert len(responses.calls) == 2
        assert not seen


@pytest.mark.asyncio
async def test_invalid_snapshot_keeps_previous_boosts_for_next_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(boosts(2), {"data": [{"type": "coins"}]}, boosts(3))
    clock = ManualClock()
    client = offline_client(monkeypatch, responses, clock=clock)
    received: asyncio.Queue[tuple[tuple[GlobalBoost, ...], tuple[GlobalBoost, ...]]] = (
        asyncio.Queue()
    )

    @client.event
    async def on_global_boosts_changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await received.put((before, after))

    async with client:
        await settle()
        clock.advance(60)
        await settle()
        assert PollingResource.GLOBAL_BOOSTS in client.last_poll_errors
        assert received.empty()
        clock.advance(60)
        before, after = await asyncio.wait_for(received.get(), timeout=1)
        assert before[0].multiplier == 2
        assert after[0].multiplier == 3
        await settle()
        assert not client.last_poll_errors


@pytest.mark.asyncio
async def test_full_client_queue_retries_retained_batch_without_fetching_over_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(boosts(2), boosts(3))
    clock = ManualClock()
    client = offline_client(
        monkeypatch,
        responses,
        clock=clock,
        events=EventConfig(emit_initial=True, max_pending_deliveries=1),
    )
    received: asyncio.Queue[tuple[GlobalBoost, ...]] = asyncio.Queue()
    release = asyncio.Event()

    @client.event
    async def on_global_boosts_changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await received.put(after)
        if after[0].multiplier == 2:
            await release.wait()

    async with client:
        assert (await asyncio.wait_for(received.get(), timeout=1))[0].multiplier == 2
        await settle()
        assert (await client.count_pending_events()) == 1
        clock.advance(60)
        await settle()
        assert isinstance(
            client.last_poll_errors[PollingResource.GLOBAL_BOOSTS], BufferError
        )
        assert len(responses.calls) == 2
        release.set()
        await settle()
        clock.advance(60)
        assert (await asyncio.wait_for(received.get(), timeout=1))[0].multiplier == 3
        await settle()
        assert len(responses.calls) == 2
        assert (await client.count_pending_events()) == 0
        assert not client.last_poll_errors


@pytest.mark.asyncio
async def test_disabled_polling_keeps_listener_without_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses()
    client = offline_client(
        monkeypatch, responses, polling=PollingConfig(lottery=DisabledPolling())
    )

    @client.event
    async def on_lottery_result(result: LotteryResult) -> None:
        raise AssertionError("disabled resource was delivered")

    async with client:
        await settle()
        assert not client.active_polling_resources
        assert not responses.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_best_effort_callback_failures_are_discarded_and_delivery_continues(
    monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    client = offline_client(
        monkeypatch, Responses(blogs(2, 1)), events=EventConfig(emit_initial=True)
    )
    seen: list[str] = []
    finished = asyncio.Event()

    @client.event
    async def on_blog_published(blog: Blog) -> None:
        seen.append(blog.id)
        if blog.id == "1":
            if cancelled:
                raise asyncio.CancelledError
            raise RuntimeError("failed callback")
        finished.set()

    async with client:
        await asyncio.wait_for(finished.wait(), timeout=1)
        await settle()
        assert seen == ["1", "2"]
        assert await client.count_pending_events() == 0
        assert not client.paused_event_subscriptions
        assert not client.last_delivery_errors


@pytest.mark.asyncio
async def test_removing_listener_discards_queued_best_effort_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = offline_client(
        monkeypatch, Responses(blogs(2, 1)), events=EventConfig(emit_initial=True)
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    received: list[str] = []

    async def listener(blog: Blog) -> None:
        entered.set()
        await release.wait()
        received.append(blog.id)

    listener_id = client.add_listener(listener, name="blog_published")
    async with client:
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert await client.count_pending_events() == 2
            assert client.remove_listener(listener_id)
            assert await client.count_pending_events() == 1
            release.set()
            async with asyncio.timeout(1):
                while await client.count_pending_events():
                    await asyncio.sleep(0)
            assert received == ["1"]
        finally:
            release.set()


@pytest.mark.asyncio
async def test_late_lottery_retries_until_current_hour_result_then_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = CalendarClock(datetime(2026, 10, 1, 0, 59, 15, tzinfo=UTC))
    responses = Responses(lottery(0), lottery(0), lottery(0), lottery(1))
    client = offline_client(monkeypatch, responses, clock=clock)
    seen: asyncio.Queue[LotteryResult] = asyncio.Queue()

    @client.event
    async def on_lottery_result(result: LotteryResult) -> None:
        await seen.put(result)

    async with client:
        await settle()
        assert client._events.scheduler._next_deadline[PollingResource.LOTTERY] == 60
        clock.advance(60)
        await settle()
        clock.advance(60)
        await settle()
        assert seen.empty()
        clock.advance(60)
        result = await asyncio.wait_for(seen.get(), timeout=1)
        assert result.drawn_at.hour == 1
        await settle()
        assert client._events.scheduler._next_deadline[PollingResource.LOTTERY] == 3660
        assert [call.route for call in responses.calls] == [LOTTERY] * 4


@pytest.mark.parametrize(
    "policy,wall,observed,expected",
    [
        (HourlyPolling(), "2026-10-01T01:00:15", "2026-10-01T00:00:00", 60),
        (HourlyPolling(), "2026-10-01T01:06:15", "2026-10-01T00:00:00", 300),
        (HourlyPolling(), "2026-10-01T01:06:15", "2026-10-01T01:03:00", 3240),
        (DailyPolling(), "2026-10-02T00:00:15", "2026-10-01T00:00:00", 60),
        (DailyPolling(), "2026-10-02T00:06:15", "2026-10-01T00:00:00", 900),
        (DailyPolling(), "2026-10-02T12:00:15", "2026-10-02T00:00:00", 43200),
        (HourlyPolling(), "2026-10-01T01:59:55", None, 60),
    ],
)
def test_calendar_schedules_late_results_reconciliation_and_minimum_spacing(
    policy: HourlyPolling | DailyPolling,
    wall: str,
    observed: str | None,
    expected: float,
) -> None:
    def timestamp(value: str) -> float:
        return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()

    assert (
        calendar_delay(
            policy, timestamp(wall), None if observed is None else timestamp(observed)
        )
        == expected
    )


@pytest.mark.asyncio
async def test_publication_recovery_uses_one_page_per_minute_then_returns_to_hourly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    responses = Responses(blogs(1), blogs(4, 3, next_cursor=2), blogs(2, 1))
    client = offline_client(
        monkeypatch, responses, clock=clock, events=EventConfig(publication_page_size=2)
    )
    seen: list[str] = []

    @client.event
    async def on_blog_published(entry: Blog) -> None:
        seen.append(entry.id)

    async with client:
        await settle()
        clock.advance(3599)
        await settle()
        assert len(responses.calls) == 1
        clock.advance(1)
        await settle()
        assert not seen
        assert client._events.scheduler._next_deadline[PollingResource.BLOGS] == 3660
        clock.advance(60)
        await settle()
        assert seen == ["2", "3", "4"]
        assert [call.cursor for call in responses.calls] == [0, 0, 2]
        assert all(call.automatic and call.route is BLOGS for call in responses.calls)
        assert client._events.scheduler._next_deadline[PollingResource.BLOGS] == 7260


@pytest.mark.asyncio
async def test_poll_failures_are_visible_and_do_not_replace_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(blogs(1), blogs(3, 2), blogs(2, 1))
    clock = ManualClock()
    client = offline_client(
        monkeypatch, responses, clock=clock, events=EventConfig(publication_page_size=2)
    )
    seen: list[str] = []

    @client.listen("blog_published")
    async def published(entry: Blog) -> None:
        seen.append(entry.id)

    async with client:
        await settle()
        clock.advance(3600)
        await settle()
        assert isinstance(
            client.last_poll_errors[PollingResource.BLOGS], EventRecoveryError
        )
        assert not seen
        with pytest.raises(TypeError):
            cast(MutableMapping[PollingResource, Exception], client.last_poll_errors)[
                PollingResource.BLOGS
            ] = RuntimeError()
        clock.advance(3600)
        await settle()
        assert seen == ["2"]
        assert not client.last_poll_errors


@pytest.mark.asyncio
async def test_callback_can_close_its_client_and_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = offline_client(
        monkeypatch, Responses(lottery(1)), events=EventConfig(emit_initial=True)
    )
    finished = asyncio.Event()

    @client.event
    async def on_lottery_result(result: LotteryResult) -> None:
        await client.close()
        finished.set()

    try:
        await client.start()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await settle()
        assert client.is_closed
        assert await client._events.count_pending(None) == 0
        assert not client.active_polling_resources
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_startup_jitter_and_late_wakeup_never_send_catchup_bursts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def jitter(low: float, high: float) -> float:
        return 10.0

    monkeypatch.setattr("dankmemer._event_client.random.uniform", jitter)
    responses = Responses(boosts(2), boosts(3))
    clock = ManualClock()
    client = offline_client(
        monkeypatch,
        responses,
        clock=clock,
        polling=PollingConfig(startup_jitter_seconds=10),
    )

    @client.event
    async def on_global_boosts_changed(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        return None

    async with client:
        await settle()
        assert not responses.calls
        clock.advance(10)
        await settle()
        assert len(responses.calls) == 1
        clock.advance(185)
        await settle()
        assert len(responses.calls) == 2
        assert (
            client._events.scheduler._next_deadline[PollingResource.GLOBAL_BOOSTS]
            == 255
        )


@pytest.mark.asyncio
async def test_events_bypass_sdk_cache_but_share_manual_request_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    paths: list[float] = []

    async def handler(request: web.Request) -> web.Response:
        paths.append(clock.monotonic())
        return web.json_response(boosts(float(len(paths) + 1)))

    app = web.Application()
    app.router.add_get("/api/v1/global-boosts", handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(
        "dankmemer.http._transport._API_BASE_URL", str(server.make_url("/api/v1"))
    )
    monkeypatch.setattr("dankmemer.client.Admission", lambda: Admission(clock))
    try:
        async with aiohttp.ClientSession() as session:
            client = DankMemer(
                "test-token",
                session=session,
                cache=CacheConfig(global_boosts=TTLCache(ttl=timedelta(hours=1))),
                events=EventConfig(emit_initial=True),
            )
            received: asyncio.Queue[tuple[GlobalBoost, ...]] = asyncio.Queue()

            @client.event
            async def on_global_boosts_changed(
                before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
            ) -> None:
                await received.put(after)

            try:
                manual = await client.global_boosts.active()
                await client.start()
                await settle()
                assert paths == [0]
                clock.advance(59)
                await settle()
                assert paths == [0]
                clock.advance(1)
                event = await asyncio.wait_for(received.get(), timeout=1)
                assert paths == [0, 60]
                assert manual[0].multiplier == 2
                assert event[0].multiplier == 3
                assert await client.global_boosts.active() == manual
                assert paths == [0, 60]
            finally:
                await client.close()
            assert not session.closed
    finally:
        await server.close()


def test_public_event_configuration_and_registration_validation() -> None:
    async def callback() -> None:
        return None

    for invalid in (
        {"emit_initial": 1},
        {"max_pending_deliveries": 0},
        {"publication_page_size": 101},
        {"publication_page_size": 2, "max_staged_publications": 1},
    ):
        with pytest.raises(ConfigurationError):
            cast(Callable[..., EventConfig], EventConfig)(**invalid)
    DankMemer("test-token", polling=PollingConfig(drops=IntervalPolling(minutes=2)))
    client = DankMemer("test-token")
    with pytest.raises(ConfigurationError):
        client.add_listener(callback, name="drops_started")
    with pytest.raises(ConfigurationError):
        client.add_listener(
            cast(Callable[..., Awaitable[None]], lambda: None), name="lottery_result"
        )
    with pytest.raises(ConfigurationError):
        client.remove_listener(True)


@pytest.mark.asyncio
async def test_listener_changes_on_closed_client_are_rejected() -> None:
    client = DankMemer("test-token")
    await client.close()

    async def on_lottery_result(result: LotteryResult) -> None:
        return None

    with pytest.raises(LifecycleError):
        client.event(on_lottery_result)
