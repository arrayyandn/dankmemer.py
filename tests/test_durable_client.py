# pyright: reportPrivateUsage=false
import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from event_helpers import (
    CalendarClock,
    durable,
    lottery,
    offline_client,
    settle,
    wait_checkpoint,
    wait_count,
    wait_paused,
)
from resource_helpers import Responses, blogs

from dankmemer import (
    Blog,
    CallbackIntent,
    ClientState,
    CommitResult,
    DankMemer,
    DisabledPolling,
    EventConfig,
    EventDelivery,
    EventPayloadError,
    EventStore,
    GlobalBoost,
    LotteryResult,
    MemoryEventStore,
    PendingCallbackLimit,
    PollingConfig,
    PollingResource,
    StoredCallback,
    StoredCheckpoint,
)
from dankmemer._event_client import EventClient
from dankmemer.errors import ConfigurationError
from dankmemer.storage.sqlite import SqliteEventStore


@pytest.mark.asyncio
async def test_start_does_not_repeat_persistent_store_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_prepare = EventClient.prepare
    preparations = 0

    async def prepare_once(client: EventClient) -> None:
        nonlocal preparations
        preparations += 1
        if preparations > 1:
            raise RuntimeError("store unavailable on repeated preparation")
        await original_prepare(client)

    monkeypatch.setattr(EventClient, "prepare", prepare_once)
    async with await SqliteEventStore.open(tmp_path / "startup.sqlite") as store:
        dank = offline_client(
            monkeypatch, Responses(), events=durable(), event_store=store
        )
        async with dank:
            assert dank.is_running
        assert preparations == 1
        assert await store.count_pending_callbacks() == 0


@pytest.mark.asyncio
async def test_failed_context_entry_leaves_supplied_store_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with await SqliteEventStore.open(tmp_path / "failed-startup.sqlite") as store:
        dank = offline_client(
            monkeypatch, Responses(), events=durable(), event_store=store
        )

        async def fail_start() -> None:
            raise RuntimeError("event startup failed")

        monkeypatch.setattr(dank._events, "start", fail_start)
        with pytest.raises(RuntimeError, match="event startup failed"):
            async with dank:
                raise AssertionError("context body must not run")
        assert dank.is_closed
        assert await store.count_pending_callbacks() == 0


@pytest.mark.asyncio
async def test_listener_added_during_startup_has_its_baseline_prepared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    resources: list[PollingResource] = []
    responses = Responses()
    async with await SqliteEventStore.open(
        tmp_path / "startup-listeners.sqlite"
    ) as store:
        read_checkpoint = store.read_checkpoint

        async def gated_read(resource: PollingResource) -> StoredCheckpoint | None:
            resources.append(resource)
            if resource is PollingResource.GLOBAL_BOOSTS:
                entered.set()
                await release.wait()
            return await read_checkpoint(resource)

        monkeypatch.setattr(store, "read_checkpoint", gated_read)
        dank = offline_client(
            monkeypatch,
            responses,
            events=durable(),
            event_store=store,
            polling=PollingConfig(
                global_boosts=DisabledPolling(), lottery=DisabledPolling()
            ),
        )

        @dank.listen("global_boosts_changed", subscription_id="startup.boosts")
        async def boosts_changed(
            before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
        ) -> None:
            pass

        starting = asyncio.create_task(dank.start())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)

            @dank.listen("lottery_result", subscription_id="startup.lottery")
            async def lottery_result(result: LotteryResult) -> None:
                pass

            release.set()
            await asyncio.wait_for(starting, timeout=2)
            assert dank.is_running
            assert set(resources) == {
                PollingResource.GLOBAL_BOOSTS,
                PollingResource.LOTTERY,
            }
            assert not responses.calls
        finally:
            release.set()
            await dank.close()
            await asyncio.gather(starting, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_poll", [True, False])
async def test_saved_callback_and_baseline_resume_in_new_public_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, disable_poll: bool
) -> None:
    path = tmp_path / "events.sqlite"
    clock = CalendarClock(datetime(2026, 10, 1, 0, 59, 15, tzinfo=UTC))
    first_requests = Responses(lottery(0), lottery(1))
    async with await SqliteEventStore.open(path) as store:
        first = offline_client(
            monkeypatch,
            first_requests,
            clock=clock,
            events=durable(),
            event_store=store,
        )

        @first.listen("lottery_result", subscription_id="alerts.lottery")
        async def fail(result: LotteryResult) -> None:
            raise RuntimeError("callback unavailable")

        async with first:
            await wait_checkpoint(store, PollingResource.LOTTERY, 1)
            await settle()
            clock.advance(60)
            await wait_paused(first, "alerts.lottery")
            assert isinstance(
                first.last_delivery_errors["alerts.lottery"], RuntimeError
            )
            assert (
                await first.count_pending_events(subscription_id="alerts.lottery") == 1
            )
            assert await first.count_pending_events() == 1
        assert await store.count_pending_callbacks() == 1
        assert (await store.read_checkpoint(PollingResource.LOTTERY)) is not None

    requests = Responses(lottery(1))
    policy = (
        PollingConfig(lottery=DisabledPolling()) if disable_poll else PollingConfig()
    )
    received: asyncio.Queue[LotteryResult] = asyncio.Queue()
    async with await SqliteEventStore.open(path) as reopened:
        second = offline_client(
            monkeypatch,
            requests,
            events=durable(emit_initial=True),
            polling=policy,
            event_store=reopened,
        )

        @second.listen("lottery_result", subscription_id="alerts.lottery")
        async def renamed_handler(result: LotteryResult) -> None:
            await received.put(result)

        async with second:
            result = await asyncio.wait_for(received.get(), timeout=3)
            assert result.drawn_at == datetime(2026, 10, 1, 1, tzinfo=UTC)
            await wait_count(reopened, 0)
            if disable_poll:
                assert not requests.calls
                assert not second.active_polling_resources
            else:
                await wait_checkpoint(reopened, PollingResource.LOTTERY, 3)
                await settle()
                assert len(requests.calls) == 1
            assert received.empty()
            assert not second.last_delivery_errors


@pytest.mark.asyncio
async def test_publication_recovery_restarts_from_saved_checkpoint_after_interruption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "publications.sqlite"
    clock = CalendarClock(datetime(2026, 10, 1, tzinfo=UTC))
    async with await SqliteEventStore.open(path) as store:
        requests = Responses(blogs(1), blogs(3, 2, next_cursor=2))
        first = offline_client(
            monkeypatch,
            requests,
            clock=clock,
            events=durable(page_size=2),
            event_store=store,
        )

        @first.listen("blog_published", subscription_id="alerts.blog")
        async def unused(blog: Blog) -> None:
            raise AssertionError("partial recovery delivered an entry")

        async with first:
            await wait_checkpoint(store, PollingResource.BLOGS, 1)
            await settle()
            clock.advance(3600)
            async with asyncio.timeout(3):
                while not first._events._sources[PollingResource.BLOGS].recovering:
                    await asyncio.sleep(0.001)
            assert await store.count_pending_callbacks() == 0
            checkpoint = await store.read_checkpoint(PollingResource.BLOGS)
            assert checkpoint is not None and checkpoint.version == 1

    next_clock = CalendarClock(datetime(2026, 10, 1, 1, tzinfo=UTC))
    requests = Responses(blogs(4, 3, next_cursor=2), blogs(2, 1))
    seen: list[str] = []
    async with await SqliteEventStore.open(path) as store:
        second = offline_client(
            monkeypatch,
            requests,
            clock=next_clock,
            events=durable(page_size=2),
            event_store=store,
        )

        @second.listen("blog_published", subscription_id="alerts.blog")
        async def published(blog: Blog) -> None:
            seen.append(blog.id)

        async with second:
            async with asyncio.timeout(3):
                while not second._events._sources[PollingResource.BLOGS].recovering:
                    await asyncio.sleep(0.001)
            assert not seen
            await settle()
            next_clock.advance(60)
            await wait_checkpoint(store, PollingResource.BLOGS, 2)
            await wait_count(store, 0)
            assert seen == ["2", "3", "4"]
            assert [call.cursor for call in requests.calls] == [0, 2]


@pytest.mark.asyncio
async def test_retry_pending_resumes_failed_subscription_in_order_without_api_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests = Responses(blogs(3, 2, 1))
    seen: list[str] = []
    async with await SqliteEventStore.open(tmp_path / "retry.sqlite") as store:
        client = offline_client(
            monkeypatch, requests, events=durable(emit_initial=True), event_store=store
        )

        @client.listen("blog_published", subscription_id="alerts.blog")
        async def handler(blog: Blog) -> None:
            seen.append(blog.id)
            if len(seen) == 1:
                raise ValueError("temporary failure")

        async with client:
            await wait_paused(client, "alerts.blog")
            assert seen == ["1"]
            assert await client.count_pending_events() == 3
            client.retry_pending("alerts.blog")
            await wait_count(store, 0)
            assert seen == ["1", "1", "2", "3"]
            assert len(requests.calls) == 1
            assert not client.paused_event_subscriptions
            assert not client.last_delivery_errors


@pytest.mark.asyncio
async def test_removed_durable_listener_keeps_calls_and_replacement_resumes_same_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered = asyncio.Event()
    replayed = asyncio.Event()
    async with await SqliteEventStore.open(tmp_path / "remove.sqlite") as store:
        client = offline_client(
            monkeypatch,
            Responses(lottery(1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        async def blocked(result: LotteryResult) -> None:
            entered.set()
            await asyncio.Event().wait()

        async def replacement(result: LotteryResult) -> None:
            replayed.set()

        client.add_listener(
            blocked, name="lottery_result", subscription_id="alerts.lottery"
        )
        async with client:
            await asyncio.wait_for(entered.wait(), timeout=3)
            assert client.remove_listener(blocked, name="lottery_result")
            assert await store.count_pending_callbacks() == 1
            client.add_listener(
                replacement, name="lottery_result", subscription_id="alerts.lottery"
            )
            await asyncio.wait_for(replayed.wait(), timeout=3)
            await wait_count(store, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("grace_seconds", [None, 1.0])
async def test_durable_callback_can_close_client_and_then_acknowledge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, grace_seconds: float | None
) -> None:
    finished = asyncio.Event()
    async with await SqliteEventStore.open(tmp_path / "close.sqlite") as store:
        client = offline_client(
            monkeypatch,
            Responses(lottery(1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        @client.event
        async def on_lottery_result(result: LotteryResult) -> None:
            await client.close(grace_seconds=grace_seconds)
            finished.set()

        try:
            await client.start()
            await asyncio.wait_for(finished.wait(), timeout=3)
            await wait_count(store, 0)
            assert client.is_closed
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_pending_capacity_rejects_checkpoint_and_retains_detected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = CalendarClock(datetime(2026, 10, 1, 0, 59, 15, tzinfo=UTC))
    requests = Responses(lottery(0), lottery(1))
    release = asyncio.Event()
    received: asyncio.Queue[int] = asyncio.Queue()
    async with await SqliteEventStore.open(tmp_path / "capacity.sqlite") as store:
        client = offline_client(
            monkeypatch,
            requests,
            clock=clock,
            events=durable(emit_initial=True, maximum=1),
            event_store=store,
        )

        @client.event
        async def on_lottery_result(result: LotteryResult) -> None:
            await received.put(result.drawn_at.hour)
            if result.drawn_at.hour == 0:
                await release.wait()

        async with client:
            assert await asyncio.wait_for(received.get(), timeout=3) == 0
            await settle()
            clock.advance(60)
            async with asyncio.timeout(3):
                while PollingResource.LOTTERY not in client.last_poll_errors:
                    await asyncio.sleep(0.001)
            assert isinstance(
                client.last_poll_errors[PollingResource.LOTTERY], PendingCallbackLimit
            )
            checkpoint = await store.read_checkpoint(PollingResource.LOTTERY)
            assert checkpoint is not None and checkpoint.version == 1
            assert await store.count_pending_callbacks() == 1
            release.set()
            await wait_count(store, 0)
            await settle()
            clock.advance(60)
            assert await asyncio.wait_for(received.get(), timeout=3) == 1
            await wait_count(store, 0)
            assert len(requests.calls) == 2
            assert not client.last_poll_errors


@pytest.mark.asyncio
async def test_invalid_checkpoint_blocks_start_without_creating_session_or_polling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests = Responses()
    async with await SqliteEventStore.open(tmp_path / "invalid.sqlite") as store:
        await store.commit(
            PollingResource.LOTTERY,
            expected_version=None,
            payload=b'{"version":999}',
            revision=None,
            callbacks=(),
        )
        client = offline_client(
            monkeypatch, requests, events=durable(), event_store=store
        )

        @client.event
        async def on_lottery_result(result: LotteryResult) -> None:
            raise AssertionError("invalid checkpoint was delivered")

        try:
            with pytest.raises(EventPayloadError):
                await client.start()
            assert client.state is ClientState.NEW
            assert client._session is None
            assert not requests.calls
            checkpoint = await store.read_checkpoint(PollingResource.LOTTERY)
            assert checkpoint is not None and checkpoint.payload == b'{"version":999}'
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_process_crash_after_callback_success_replays_that_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "crash.sqlite"
    marker = tmp_path / "callback.txt"
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tests" / "crash_event_worker.py"),
            str(path),
            str(marker),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert marker.read_text(encoding="utf-8") == "100"
    requests = Responses()
    replayed = asyncio.Event()
    async with await SqliteEventStore.open(path) as store:
        assert await store.count_pending_callbacks("crash.lottery") == 1
        client = offline_client(
            monkeypatch,
            requests,
            events=durable(),
            polling=PollingConfig(lottery=DisabledPolling()),
            event_store=store,
        )

        @client.event(subscription_id="crash.lottery")
        async def on_lottery_result(result: LotteryResult) -> None:
            assert result.winnings == 100
            replayed.set()

        async with client:
            await asyncio.wait_for(replayed.wait(), timeout=3)
            await wait_count(store, 0)
            assert not requests.calls


class CustomStore:
    def __init__(self, backing: SqliteEventStore) -> None:
        self.backing = backing

    @property
    def durable(self) -> bool:
        return True

    async def read_checkpoint(
        self, resource: PollingResource
    ) -> StoredCheckpoint | None:
        return await self.backing.read_checkpoint(resource)

    async def commit(
        self,
        resource: PollingResource,
        *,
        expected_version: int | None,
        payload: bytes,
        revision: int | None,
        callbacks: Sequence[CallbackIntent],
        max_pending_callbacks: int | None = None,
    ) -> CommitResult | None:
        return await self.backing.commit(
            resource,
            expected_version=expected_version,
            payload=payload,
            revision=revision,
            callbacks=callbacks,
            max_pending_callbacks=max_pending_callbacks,
        )

    async def pending_callbacks(
        self, subscription_id: str, *, limit: int
    ) -> tuple[StoredCallback, ...]:
        return await self.backing.pending_callbacks(subscription_id, limit=limit)

    async def acknowledge(self, subscription_id: str, callback_id: int) -> bool:
        return await self.backing.acknowledge(subscription_id, callback_id)

    async def count_pending_callbacks(self, subscription_id: str | None = None) -> int:
        return await self.backing.count_pending_callbacks(subscription_id)


@pytest.mark.asyncio
async def test_custom_store_uses_protocol_without_inheriting_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with await SqliteEventStore.open(tmp_path / "custom.sqlite") as backing:
        store: EventStore = CustomStore(backing)
        assert isinstance(store, EventStore)
        client = offline_client(
            monkeypatch,
            Responses(lottery(1)),
            events=durable(emit_initial=True),
            event_store=store,
        )
        called = asyncio.Event()

        @client.event
        async def on_lottery_result(result: LotteryResult) -> None:
            called.set()

        async with client:
            await asyncio.wait_for(called.wait(), timeout=3)
            await wait_count(store, 0)
        assert await store.count_pending_callbacks() == 0


@pytest.mark.asyncio
async def test_durable_ids_are_explicit_unique_and_cannot_change_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def callback(result: LotteryResult) -> None:
        return None

    async def second(result: LotteryResult) -> None:
        return None

    async with await SqliteEventStore.open(tmp_path / "ids.sqlite") as store:
        client = offline_client(
            monkeypatch, Responses(), events=durable(), event_store=store
        )
        try:
            with pytest.raises(ConfigurationError, match="stable subscription_id"):
                client.add_listener(callback, name="lottery_result")
            listener_id = client.add_listener(
                callback, name="lottery_result", subscription_id="alerts"
            )
            assert (
                client.add_listener(
                    callback, name="lottery_result", subscription_id="alerts"
                )
                == listener_id
            )
            with pytest.raises(ConfigurationError, match="already registered"):
                client.add_listener(
                    second, name="lottery_result", subscription_id="alerts"
                )
            assert client.remove_listener(listener_id)
            with pytest.raises(ConfigurationError, match="another event"):
                client.add_listener(
                    callback, name="blog_published", subscription_id="alerts"
                )
        finally:
            await client.close()


def test_durable_mode_requires_persistence_and_correct_configuration() -> None:
    with pytest.raises(ConfigurationError, match="persistent"):
        DankMemer("test-token", events=durable())
    with pytest.raises(ConfigurationError, match="persistent"):
        DankMemer("test-token", events=durable(), event_store=MemoryEventStore())
    with pytest.raises(ConfigurationError, match="requires EventDelivery"):
        DankMemer("test-token", event_store=MemoryEventStore())
    with pytest.raises(ConfigurationError, match="EventStore"):
        DankMemer(
            "test-token", events=durable(), event_store=cast(EventStore, object())
        )
    with pytest.raises(ConfigurationError, match="EventDelivery"):
        EventConfig(delivery=cast(EventDelivery, "durable"))
