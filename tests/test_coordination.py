# pyright: reportPrivateUsage=false

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

import asqlite
import asyncpg
import pytest
import pytest_asyncio
from asyncpg.pool import PoolConnectionProxy
from event_helpers import (
    CalendarClock,
    boosts,
    durable,
    lottery,
    settle,
    wait_checkpoint,
    wait_count,
    wait_paused,
)
from resource_helpers import Responses

from dankmemer import (
    CoordinatedEventStore,
    CoordinationConfig,
    CoordinationMode,
    DailyPolling,
    DankMemer,
    DisabledPolling,
    GlobalBoost,
    HourlyPolling,
    IntervalPolling,
    LotteryResult,
    PollingConfig,
    PollingResource,
)
from dankmemer._coordination import Coordinator, Lease, LeaseLost
from dankmemer._coordination_storage import SharedUpdate
from dankmemer._event_client import DEPENDENCIES, polling_policies
from dankmemer._postgres_storage import PostgresEventStore
from dankmemer._sqlite_storage import SqliteEventStore
from dankmemer.errors import ConfigurationError, DankMemerTimeoutError
from dankmemer.http._admission import Admission

_T = TypeVar("_T")


class _TimedSqliteStore(SqliteEventStore):
    test_clock: CalendarClock

    async def _shared_state(
        self, connection: asqlite.Connection
    ) -> tuple[bytes | None, float]:
        state, _ = await super()._shared_state(connection)
        return state, self.test_clock.time()


class _TimedPostgresStore(PostgresEventStore):
    test_clock: CalendarClock

    async def _shared_state(
        self, conn: PoolConnectionProxy
    ) -> tuple[bytes | None, float]:
        state, _ = await super()._shared_state(conn)
        return state, self.test_clock.time()


@dataclass(frozen=True)
class Stores:
    left: CoordinatedEventStore
    right: CoordinatedEventStore
    clock: CalendarClock


@pytest_asyncio.fixture(params=("sqlite", "postgres"))
async def shared_stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Stores]:
    clock = CalendarClock(datetime(2026, 10, 1, tzinfo=UTC))
    if request.param == "sqlite":
        path = tmp_path / "shared.sqlite"
        async with await _TimedSqliteStore.open(path) as left:
            async with await _TimedSqliteStore.open(path) as right:
                left.test_clock = right.test_clock = clock
                yield Stores(left, right, clock)
        return
    dsn = os.environ.get("DANKMEMER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set DANKMEMER_TEST_POSTGRES_DSN for PostgreSQL integration")
    schema = "shared_" + uuid4().hex
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        async with await _TimedPostgresStore.open(dsn, schema=schema) as pg_left:
            async with await _TimedPostgresStore.open(dsn, schema=schema) as pg_right:
                pg_left.test_clock = pg_right.test_clock = clock
                yield Stores(pg_left, pg_right, clock)
    finally:
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()


def shared(*, application_id: str = "test-application") -> CoordinationConfig:
    return CoordinationConfig(
        mode=CoordinationMode.SHARED, application_id=application_id
    )


def shared_client(
    monkeypatch: pytest.MonkeyPatch,
    stores: Stores,
    store: CoordinatedEventStore,
    responses: Responses,
    *,
    polling: PollingConfig | None = None,
    emit_initial: bool = True,
) -> DankMemer:
    monkeypatch.setattr("dankmemer.client.Admission", lambda: Admission(stores.clock))
    monkeypatch.setattr(DankMemer, "_request_json", responses)
    return DankMemer(
        "test-token",
        event_store=store,
        events=durable(emit_initial=emit_initial),
        coordination=shared(),
        polling=polling,
        silent=True,
    )


def coordinator(
    store: CoordinatedEventStore,
    clock: CalendarClock,
    *,
    token: str = "test-token",
    application_id: str = "test-application",
    polling: PollingConfig | None = None,
) -> Coordinator:
    return Coordinator(
        store,
        shared(application_id=application_id),
        durable(),
        polling_policies(polling or PollingConfig()),
        DEPENDENCIES,
        token,
        clock,
        logging.getLogger("test"),
    )


async def advance(clock: CalendarClock, seconds: int) -> None:
    # Let both database connections finish renewals before advancing again.
    for _ in range(seconds // 5):
        clock.advance(5)
        await asyncio.sleep(0.025)


async def drive_until(clock: CalendarClock, condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not condition():
            clock.advance(1)
            # Shorter sleeps can return immediately on Windows 3.11 and 3.12.
            await asyncio.sleep(0.025)


def track_finished_poll(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    polled = asyncio.Event()
    original_finish = Coordinator.finish_poll

    async def finish(
        coordinator: Coordinator,
        resource: PollingResource,
        lease: Lease,
        delay: Callable[[float], float],
    ) -> None:
        await original_finish(coordinator, resource, lease, delay)
        polled.set()

    monkeypatch.setattr(Coordinator, "finish_poll", finish)
    return polled


@pytest.mark.asyncio
async def test_one_shared_poller_fans_out_to_distinct_subscriptions(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    polled = track_finished_poll(monkeypatch)
    responses = Responses(boosts(2), boosts(3))
    left = shared_client(monkeypatch, stores, stores.left, responses)
    right = shared_client(monkeypatch, stores, stores.right, responses)
    received: asyncio.Queue[str] = asyncio.Queue()

    @left.listen("global_boosts_changed", subscription_id="consumer.left")
    async def first(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await received.put("left")

    @right.listen("global_boosts_changed", subscription_id="consumer.right")
    async def second(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await received.put("right")

    await left._events.prepare()
    await right._events.prepare()
    try:
        await asyncio.gather(left.start(), right.start())
        await wait_checkpoint(stores.left, PollingResource.GLOBAL_BOOSTS, 1)
        # The checkpoint can commit before the next poll time has been saved.
        await asyncio.wait_for(polled.wait(), 3)
        await advance(stores.clock, 5)
        seen = {await asyncio.wait_for(received.get(), 3) for _ in range(2)}
        assert seen == {"left", "right"}
        assert len(responses.calls) == 1
        assert isinstance(left.active_polling_resources, frozenset)
        await wait_count(stores.left, 0)
        await advance(stores.clock, 55)
        await drive_until(stores.clock, lambda: len(responses.calls) == 2)
        await wait_checkpoint(stores.left, PollingResource.GLOBAL_BOOSTS, 2)
        await advance(stores.clock, 5)
        seen = {await asyncio.wait_for(received.get(), 3) for _ in range(2)}
        assert seen == {"left", "right"}
        assert len(responses.calls) == 2
        assert received.empty()
        assert not left.last_poll_errors and not right.last_poll_errors
    finally:
        await asyncio.gather(left.close(), right.close())


@pytest.mark.asyncio
async def test_reusing_subscription_id_shares_one_consumer(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    responses = Responses(lottery(1))
    left = shared_client(monkeypatch, stores, stores.left, responses)
    right = shared_client(monkeypatch, stores, stores.right, responses)
    entered: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()

    @left.listen("lottery_result", subscription_id="one.consumer")
    async def first(result: LotteryResult) -> None:
        await entered.put("left")
        await release.wait()

    @right.listen("lottery_result", subscription_id="one.consumer")
    async def second(result: LotteryResult) -> None:
        await entered.put("right")
        await release.wait()

    await left._events.prepare()
    await right._events.prepare()
    try:
        await asyncio.gather(left.start(), right.start())
        await drive_until(stores.clock, lambda: not entered.empty())
        assert await asyncio.wait_for(entered.get(), 3) in {"left", "right"}
        await advance(stores.clock, 35)
        assert entered.empty()
        assert await stores.left.count_pending_callbacks() == 1
        assert len(responses.calls) == 1
        release.set()
        await wait_count(stores.left, 0)
        assert entered.empty()
    finally:
        release.set()
        await asyncio.gather(left.close(), right.close())


@pytest.mark.asyncio
async def test_poller_takeover_preserves_interval_and_offline_consumer(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    polled = track_finished_poll(monkeypatch)
    responses = Responses(boosts(2), boosts(3))
    left = shared_client(
        monkeypatch, stores, stores.left, responses, emit_initial=False
    )
    right = shared_client(
        monkeypatch, stores, stores.right, responses, emit_initial=False
    )
    seen: asyncio.Queue[str] = asyncio.Queue()

    @left.listen("global_boosts_changed", subscription_id="offline")
    async def first(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await seen.put("offline")

    @right.listen("global_boosts_changed", subscription_id="online")
    async def second(
        before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        await seen.put("online")

    await left.start()
    await wait_checkpoint(stores.left, PollingResource.GLOBAL_BOOSTS, 1)
    await asyncio.wait_for(polled.wait(), 3)
    await right.start()
    await left.close()
    try:
        await advance(stores.clock, 55)
        assert len(responses.calls) == 1
        await advance(stores.clock, 5)
        # Ownership checks can resume just after the interval becomes due.
        await drive_until(stores.clock, lambda: len(responses.calls) == 2)
        await wait_checkpoint(stores.left, PollingResource.GLOBAL_BOOSTS, 2)
        await advance(stores.clock, 5)
        assert await asyncio.wait_for(seen.get(), 3) == "online"
        await wait_count(stores.left, 1, "offline")
        assert len(responses.calls) == 2
        restarted = shared_client(
            monkeypatch,
            stores,
            stores.left,
            responses,
            emit_initial=False,
            polling=PollingConfig(global_boosts=DisabledPolling()),
        )
        restarted.add_listener(
            first, name="global_boosts_changed", subscription_id="offline"
        )
        async with restarted:
            assert await asyncio.wait_for(seen.get(), 3) == "offline"
            await wait_count(stores.left, 0)
            assert len(responses.calls) == 2
    finally:
        await right.close()


@pytest.mark.asyncio
async def test_shared_pause_survives_restart_and_explicit_retry_resumes(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    responses = Responses(lottery(1))
    left = shared_client(monkeypatch, stores, stores.left, responses)
    calls: list[str] = []

    @left.listen("lottery_result", subscription_id="paused")
    async def fail(result: LotteryResult) -> None:
        calls.append("failed")
        raise RuntimeError("temporarily unavailable")

    await left.start()
    try:
        await drive_until(
            stores.clock, lambda: "paused" in left.paused_event_subscriptions
        )
        await wait_paused(left, "paused")
        assert left._coordinator is not None
        async with asyncio.timeout(3):
            while not left._coordinator.paused("paused"):
                await asyncio.sleep(0.001)
    finally:
        await left.close()
    right = shared_client(monkeypatch, stores, stores.right, responses)

    @right.listen("lottery_result", subscription_id="paused")
    async def succeed(result: LotteryResult) -> None:
        calls.append("retried")

    async with right:
        await settle()
        assert "paused" in right.paused_event_subscriptions
        assert calls == ["failed"]
        assert await right.count_pending_events() == 1
        right.retry_pending("paused")
        await advance(stores.clock, 5)
        await wait_count(stores.left, 0)
        assert calls == ["failed", "retried"]
        assert not right.paused_event_subscriptions


@pytest.mark.asyncio
async def test_callback_can_close_client_and_still_acknowledge(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    client = shared_client(monkeypatch, stores, stores.left, Responses(lottery(1)))
    closed = asyncio.Event()

    @client.listen("lottery_result", subscription_id="close.inside")
    async def close(result: LotteryResult) -> None:
        await client.close()
        closed.set()

    await client.start()
    try:
        await drive_until(stores.clock, closed.is_set)
        await wait_count(stores.left, 0)
        assert client._coordinator is not None
        assert client._coordinator._close_task is not None
        await asyncio.wait_for(asyncio.shield(client._coordinator._close_task), 3)
        assert not client._coordinator._owned
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_expired_owner_cannot_commit_or_acknowledge(
    shared_stores: Stores,
) -> None:
    stores = shared_stores
    left = coordinator(stores.left, stores.clock)
    right = coordinator(stores.right, stores.clock)
    left.register("consumer", "lottery_result")
    right.register("consumer", "lottery_result")
    await left.prepare()
    await right.prepare()
    old_poll, due = await left.claim_poll(PollingResource.LOTTERY)
    old_delivery = await left.claim_subscription("consumer")
    assert old_poll is not None and due and old_delivery is not None
    committed = await left.commit(
        PollingResource.LOTTERY,
        old_poll,
        expected_version=None,
        payload=b"first",
        revision=None,
        emissions=(("lottery_result", b"arguments"),),
        maximum=10,
    )
    assert committed is not None
    stores.clock.advance(31)
    new_delivery = await right.claim_subscription("consumer")
    assert new_delivery is not None and new_delivery.token != old_delivery.token
    with pytest.raises(LeaseLost):
        await left.acknowledge("consumer", committed.callbacks[0].id, old_delivery)
    with pytest.raises(LeaseLost):
        await left.commit(
            PollingResource.LOTTERY,
            old_poll,
            expected_version=1,
            payload=b"stale",
            revision=None,
            emissions=(),
            maximum=10,
        )
    assert (
        await stores.left.read_checkpoint(PollingResource.LOTTERY)
        == committed.checkpoint
    )
    assert await stores.left.count_pending_callbacks() == 1
    assert await right.acknowledge("consumer", committed.callbacks[0].id, new_delivery)
    await asyncio.gather(left.close(), right.close())


@pytest.mark.asyncio
async def test_shared_budget_survives_takeover_and_manual_requests(
    shared_stores: Stores,
) -> None:
    stores = shared_stores
    left = coordinator(stores.left, stores.clock)
    right = coordinator(stores.right, stores.clock)
    await left.acquire(
        path="/lottery",
        resource=PollingResource.LOTTERY,
        automatic=False,
        deadline=None,
    )
    await right.prepare()
    with pytest.raises(DankMemerTimeoutError):
        await right.acquire(
            path="/lottery",
            resource=PollingResource.LOTTERY,
            automatic=True,
            deadline=stores.clock.monotonic() + 30,
        )
    task = asyncio.create_task(
        right.acquire(
            path="/lottery",
            resource=PollingResource.LOTTERY,
            automatic=True,
            deadline=None,
        )
    )
    await asyncio.sleep(0.025)
    stores.clock.advance(59)
    await asyncio.sleep(0.025)
    assert not task.done()
    stores.clock.advance(1)
    await asyncio.wait_for(task, 3)
    await asyncio.gather(left.close(), right.close())


@pytest.mark.asyncio
async def test_shared_budget_wait_counts_time_spent_committing(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    owner = coordinator(stores.left, stores.clock)
    waiter = coordinator(stores.right, stores.clock)
    await owner.acquire(
        path="/lottery",
        resource=PollingResource.LOTTERY,
        automatic=False,
        deadline=None,
    )
    await waiter.prepare()
    original = stores.right.coordinate
    updated = asyncio.Event()
    release = asyncio.Event()
    delay_once = True

    async def delayed_commit(update: SharedUpdate[_T]) -> _T:
        nonlocal delay_once
        result = await original(update)
        if delay_once:
            delay_once = False
            updated.set()
            await release.wait()
        return result

    monkeypatch.setattr(stores.right, "coordinate", delayed_commit)
    task = asyncio.create_task(
        waiter.acquire(
            path="/lottery",
            resource=PollingResource.LOTTERY,
            automatic=True,
            deadline=None,
        )
    )
    try:
        await asyncio.wait_for(updated.wait(), 3)
        # The wait was calculated at zero, but the commit finishes at 59.
        stores.clock.advance(59)
        release.set()
        await asyncio.sleep(0.025)
        assert not task.done()
        stores.clock.advance(1)
        await asyncio.wait_for(task, 3)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(owner.close(), waiter.close())


@pytest.mark.asyncio
async def test_shared_headers_handle_raised_limits_without_saving_tokens(
    shared_stores: Stores,
) -> None:
    stores = shared_stores
    left = coordinator(stores.left, stores.clock, token="secret-that-must-not-be-saved")
    await left.acquire(path="/items", resource=None, automatic=False, deadline=None)
    await left.observe(
        {
            "X-RateLimit-Limit-Minute": "120",
            "X-RateLimit-Remaining-Minute": "119",
            "X-RateLimit-Limit-Day": "20000",
            "X-RateLimit-Remaining-Day": "19999",
        }
    )
    saved = await stores.left.coordinate(lambda state, now: (state or b"", state))
    assert saved is not None and b"secret-that-must-not-be-saved" not in saved
    assert left._state is not None
    assert left._state.budget.dump()["minute"] == {
        "limit": 120,
        "remaining": 119,
        "reset": stores.clock.time() + 60,
    }
    await left.close()


@pytest.mark.asyncio
async def test_application_policy_and_subscription_bindings_fail_closed(
    shared_stores: Stores,
) -> None:
    stores = shared_stores
    left = coordinator(stores.left, stores.clock)
    left.register("stable", "lottery_result")
    await left.prepare()
    wrong_app = coordinator(stores.right, stores.clock, application_id="another")
    with pytest.raises(ConfigurationError, match="another API application"):
        await wrong_app.prepare()
    equivalent = coordinator(
        stores.right,
        stores.clock,
        polling=PollingConfig(
            lottery=HourlyPolling(offset_seconds=15, retry_seconds=60),
            merchant_trades=DailyPolling(offset_seconds=15, retry_seconds=60),
        ),
    )
    await equivalent.prepare()
    await equivalent.close()
    wrong_policy = coordinator(
        stores.right,
        stores.clock,
        polling=PollingConfig(blogs=IntervalPolling(hours=2)),
    )
    with pytest.raises(ConfigurationError, match="polling settings differ"):
        await wrong_policy.prepare()
    wrong_event = coordinator(stores.right, stores.clock)
    wrong_event.register("stable", "merchant_rotation")
    with pytest.raises(ConfigurationError, match="another event"):
        await wrong_event.prepare()
    assert await stores.left.count_pending_callbacks() == 0
    await left.close()


@pytest.mark.asyncio
async def test_public_sync_saves_offline_registration_and_removal(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    responses = Responses(lottery(1))
    poller = shared_client(monkeypatch, stores, stores.left, responses)
    consumer = shared_client(
        monkeypatch,
        stores,
        stores.right,
        responses,
        polling=PollingConfig(lottery=DisabledPolling()),
    )
    received: asyncio.Queue[LotteryResult] = asyncio.Queue()

    async def callback(result: LotteryResult) -> None:
        await received.put(result)

    listener_id = consumer.add_listener(
        callback,
        name="lottery_result",
        subscription_id="offline.synced",
    )
    await poller.start()
    try:
        await consumer.sync_event_subscriptions()
        assert not responses.calls
        await drive_until(stores.clock, lambda: bool(responses.calls))
        await wait_count(stores.left, 1)
        await consumer.start()
        await drive_until(stores.clock, lambda: not received.empty())
        assert (await received.get()).winnings == 100
        await wait_count(stores.left, 0)
        assert consumer.remove_listener(listener_id)
        await consumer.sync_event_subscriptions()
        await drive_until(stores.clock, lambda: not poller.active_polling_resources)
        assert len(responses.calls) == 1
        assert consumer.last_coordination_error is None
    finally:
        await asyncio.gather(poller.close(), consumer.close())


@pytest.mark.asyncio
async def test_removing_own_listener_keeps_ownership_until_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, shared_stores: Stores
) -> None:
    stores = shared_stores
    responses = Responses(lottery(1))
    client = shared_client(monkeypatch, stores, stores.left, responses)
    completed = asyncio.Event()
    listener_id = 0

    async def callback(result: LotteryResult) -> None:
        assert client.remove_listener(listener_id)
        await client.sync_event_subscriptions()
        completed.set()

    listener_id = client.add_listener(
        callback,
        name="lottery_result",
        subscription_id="remove.self",
    )
    try:
        await client.start()
        await drive_until(stores.clock, completed.is_set)
        await wait_count(stores.left, 0)
        assert not client.last_delivery_errors
        coordinator = client._coordinator
        assert coordinator is not None
        await drive_until(
            stores.clock,
            lambda: not coordinator.owns("subscription:remove.self"),
        )
        assert not coordinator.owns("subscription:remove.self")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_shared_keys_keep_inflight_reservations_when_limits_increase(
    shared_stores: Stores,
) -> None:
    stores = shared_stores
    left = coordinator(stores.left, stores.clock, token="key.left")
    right = coordinator(stores.right, stores.clock, token="key.right")
    await left.prepare()
    await right.prepare()
    started = asyncio.Event()
    release = asyncio.Event()

    async def second_request() -> None:
        await right.acquire(
            path="/items", resource=None, automatic=False, deadline=None
        )
        started.set()
        await release.wait()
        await right.observe(
            {
                "X-RateLimit-Limit-Minute": "120",
                "X-RateLimit-Remaining-Minute": "118",
                "X-RateLimit-Limit-Day": "20000",
                "X-RateLimit-Remaining-Day": "19998",
            }
        )

    await left.acquire(path="/items", resource=None, automatic=False, deadline=None)
    second = asyncio.create_task(second_request())
    try:
        await drive_until(stores.clock, started.is_set)
        await left.observe(
            {
                "X-RateLimit-Limit-Minute": "120",
                "X-RateLimit-Remaining-Minute": "119",
                "X-RateLimit-Limit-Day": "20000",
                "X-RateLimit-Remaining-Day": "19999",
            }
        )
        assert left._state is not None
        assert left._state.budget.dump()["minute"] == {
            "limit": 60,
            "remaining": 58,
            "reset": stores.clock.epoch + 60,
        }
        release.set()
        await second
        assert right._state is not None
        assert right._state.budget.dump()["minute"] == {
            "limit": 120,
            "remaining": 118,
            "reset": stores.clock.epoch + 60,
        }
        await left.cooldown(120)
        with pytest.raises(DankMemerTimeoutError):
            await right.acquire(
                path="/items",
                resource=None,
                automatic=False,
                deadline=stores.clock.monotonic() + 30,
            )
    finally:
        release.set()
        await asyncio.gather(second, return_exceptions=True)
        await asyncio.gather(left.close(), right.close())


def test_shared_configuration_requires_persistent_coordination() -> None:
    with pytest.raises(ConfigurationError, match="application_id"):
        CoordinationConfig(mode=CoordinationMode.SHARED)
    with pytest.raises(ConfigurationError, match="shared coordination requires"):
        DankMemer("test-token", coordination=shared())
    with pytest.raises(ConfigurationError):
        CoordinationConfig(lease_seconds=10, heartbeat_seconds=10)
    with pytest.raises(ConfigurationError):
        CoordinationConfig(application_id="unexpected")
