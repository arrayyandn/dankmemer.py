# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from event_helpers import ManualClock

from dankmemer._events import _DemandScheduler
from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError, LifecycleError


async def noop_listener() -> None:
    return None


async def _next_call(
    calls: asyncio.Queue[tuple[float, PollingResource, frozenset[str]]],
) -> tuple[float, PollingResource, frozenset[str]]:
    return await asyncio.wait_for(calls.get(), timeout=1)


async def _wait_for_deadline(
    scheduler: _DemandScheduler, resource: PollingResource, deadline: float
) -> None:
    for _ in range(20):
        if scheduler._next_deadline.get(resource) == deadline:
            return
        await asyncio.sleep(0)
    raise AssertionError("scheduler did not finish its poll cycle")


@pytest.mark.asyncio
async def test_only_listener_required_resources_are_polled() -> None:
    clock = ManualClock()
    calls: asyncio.Queue[tuple[float, PollingResource, frozenset[str]]] = (
        asyncio.Queue()
    )

    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        await calls.put((clock.monotonic(), resource, events))

    scheduler = _DemandScheduler(
        {
            "synthetic_started": PollingResource.DROPS,
            "synthetic_ended": PollingResource.DROPS,
            "synthetic_publication": PollingResource.BLOGS,
            "listener_error": None,
        },
        {PollingResource.DROPS: 60, PollingResource.BLOGS: 3600},
        poll,
        clock=clock,
    )
    await scheduler.start()
    assert scheduler.active_resources == frozenset()
    assert calls.empty()

    error_id = scheduler.add_listener("listener_error", noop_listener)
    assert scheduler.active_resources == frozenset()
    assert calls.empty()

    started_id = scheduler.add_listener("synthetic_started", noop_listener)
    assert await _next_call(calls) == (
        0,
        PollingResource.DROPS,
        frozenset({"synthetic_started"}),
    )
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 60)
    ended_id = scheduler.add_listener("synthetic_ended", noop_listener)
    assert scheduler.active_resources == frozenset({PollingResource.DROPS})
    assert calls.empty()

    clock.advance(60)
    assert await _next_call(calls) == (
        60,
        PollingResource.DROPS,
        frozenset({"synthetic_started", "synthetic_ended"}),
    )
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 120)

    publication_id = scheduler.add_listener("synthetic_publication", noop_listener)
    assert await _next_call(calls) == (
        60,
        PollingResource.BLOGS,
        frozenset({"synthetic_publication"}),
    )
    await _wait_for_deadline(scheduler, PollingResource.BLOGS, 3660)
    assert scheduler.remove_listener(started_id)
    assert scheduler.remove_listener(ended_id)
    assert scheduler.active_resources == frozenset({PollingResource.BLOGS})
    clock.advance(3600)
    assert await _next_call(calls) == (
        3660,
        PollingResource.BLOGS,
        frozenset({"synthetic_publication"}),
    )
    assert calls.empty()
    assert scheduler.remove_listener(publication_id)
    assert scheduler.remove_listener(error_id)
    assert not scheduler.remove_listener(publication_id)
    await scheduler.close()


@pytest.mark.asyncio
async def test_remove_and_readd_preserve_spacing_without_overlapping_workers() -> None:
    clock = ManualClock()
    entered: asyncio.Queue[int] = asyncio.Queue()
    release = asyncio.Event()
    active = 0
    maximum_active = 0
    count = 0

    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        nonlocal active, maximum_active, count
        active += 1
        maximum_active = max(maximum_active, active)
        count += 1
        await entered.put(count)
        if count == 1:
            await release.wait()
        active -= 1

    scheduler = _DemandScheduler(
        {"synthetic_started": PollingResource.DROPS},
        {PollingResource.DROPS: 60},
        poll,
        clock=clock,
    )
    first = scheduler.add_listener("synthetic_started", noop_listener)
    await scheduler.start()
    assert await asyncio.wait_for(entered.get(), timeout=1) == 1
    assert scheduler.remove_listener(first)
    second = scheduler.add_listener("synthetic_started", noop_listener)
    assert second != first
    await asyncio.sleep(0)
    assert count == 1
    release.set()
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 60)
    clock.advance(60)
    assert await asyncio.wait_for(entered.get(), timeout=1) == 2
    assert maximum_active == 1
    assert scheduler.remove_listener(second)
    await scheduler.close()


@pytest.mark.asyncio
async def test_late_poll_skips_missed_slots_instead_of_bursting() -> None:
    clock = ManualClock()
    calls: asyncio.Queue[float] = asyncio.Queue()

    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        await calls.put(clock.monotonic())

    scheduler = _DemandScheduler(
        {"synthetic_started": PollingResource.DROPS},
        {PollingResource.DROPS: 60},
        poll,
        clock=clock,
    )
    scheduler.add_listener("synthetic_started", noop_listener)
    await scheduler.start()
    assert await asyncio.wait_for(calls.get(), timeout=1) == 0
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 60)

    clock.advance(185)
    assert await asyncio.wait_for(calls.get(), timeout=1) == 185
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 240)
    assert calls.empty()
    clock.advance(55)
    assert await asyncio.wait_for(calls.get(), timeout=1) == 240
    await scheduler.close()


@pytest.mark.asyncio
async def test_poll_error_does_not_end_subscription() -> None:
    clock = ManualClock()
    attempts: asyncio.Queue[int] = asyncio.Queue()
    count = 0

    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        nonlocal count
        count += 1
        await attempts.put(count)
        if clock.monotonic() == 0:
            raise RuntimeError("synthetic poll failure")

    scheduler = _DemandScheduler(
        {"synthetic_started": PollingResource.DROPS},
        {PollingResource.DROPS: 60},
        poll,
        clock=clock,
    )
    scheduler.add_listener("synthetic_started", noop_listener)
    await scheduler.start()
    assert await asyncio.wait_for(attempts.get(), timeout=1) == 1
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 60)
    clock.advance(60)
    assert await asyncio.wait_for(attempts.get(), timeout=1) == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_unexpected_poll_cancellation_restarts_after_spacing() -> None:
    clock = ManualClock()
    attempts: asyncio.Queue[float] = asyncio.Queue()

    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        await attempts.put(clock.monotonic())
        if clock.monotonic() == 0:
            raise asyncio.CancelledError

    scheduler = _DemandScheduler(
        {"synthetic_started": PollingResource.DROPS},
        {PollingResource.DROPS: 60},
        poll,
        clock=clock,
    )
    scheduler.add_listener("synthetic_started", noop_listener)
    await scheduler.start()
    assert await asyncio.wait_for(attempts.get(), timeout=1) == 0
    await _wait_for_deadline(scheduler, PollingResource.DROPS, 60)
    assert attempts.empty()
    clock.advance(60)
    assert await asyncio.wait_for(attempts.get(), timeout=1) == 60
    await scheduler.close()


def test_demand_configuration_and_registration_reject_invalid_inputs() -> None:
    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        return None

    with pytest.raises(ConfigurationError):
        _DemandScheduler(
            {"synthetic": PollingResource.DROPS},
            {PollingResource.DROPS: 59},
            poll,
        )

    scheduler = _DemandScheduler(
        {"synthetic": PollingResource.DROPS, "disabled": PollingResource.BLOGS},
        {PollingResource.DROPS: 60},
        poll,
    )
    with pytest.raises(ConfigurationError):
        scheduler.add_listener("unknown", noop_listener)
    with pytest.raises(ConfigurationError):
        scheduler.add_listener(
            "synthetic", cast(Callable[..., Awaitable[None]], lambda: None)
        )
    first = scheduler.add_listener("synthetic", noop_listener)
    assert scheduler.add_listener("synthetic", noop_listener) == first
    scheduler.add_listener("disabled", noop_listener)
    assert scheduler.active_resources == frozenset({PollingResource.DROPS})


@pytest.mark.asyncio
async def test_closed_scheduler_rejects_new_work() -> None:
    async def poll(resource: PollingResource, events: frozenset[str]) -> None:
        return None

    scheduler = _DemandScheduler({}, {}, poll)
    await scheduler.close()
    with pytest.raises(LifecycleError):
        scheduler.add_listener("synthetic", noop_listener)
    with pytest.raises(LifecycleError):
        await scheduler.start()
