import asyncio
import logging
from collections.abc import Sequence
from typing import cast

import pytest

from dankmemer._observations import Emission, Observation
from dankmemer._storage import (
    CallbackIntent,
    CommitResult,
    MemoryEventStore,
    PendingCallbackLimit,
)
from dankmemer._stored_events import StoredEventPipeline
from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError


class _SetCodec:
    def encode_snapshot(self, value: frozenset[str]) -> bytes:
        return "\n".join(sorted(value)).encode()

    def decode_snapshot(self, payload: bytes) -> frozenset[str]:
        return frozenset(payload.decode().splitlines())

    def encode_args(self, args: tuple[object, ...]) -> bytes:
        assert len(args) == 1 and isinstance(args[0], str)
        return args[0].encode()

    def decode_args(self, payload: bytes) -> tuple[object, ...]:
        return (payload.decode(),)


def _changes(
    previous: frozenset[str] | None, current: frozenset[str], initial: bool
) -> tuple[Emission, ...]:
    before = previous if previous is not None else frozenset[str]()
    return tuple(Emission("drop_started", (name,)) for name in sorted(current - before))


def _pipeline(store: MemoryEventStore) -> StoredEventPipeline[frozenset[str]]:
    return StoredEventPipeline(PollingResource.DROPS, _changes, _SetCodec(), store)


async def _wait_for_pending(
    store: MemoryEventStore, subscription_id: str, expected: int
) -> None:
    async with asyncio.timeout(2):
        while len(await store.pending_callbacks(subscription_id, limit=10)) != expected:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_complete_observations_commit_and_deliver_without_blocking_poll() -> None:
    store = MemoryEventStore()
    pipeline = _pipeline(store)
    entered = asyncio.Event()
    release = asyncio.Event()
    slow_received: list[str] = []
    fast_received: list[str] = []

    async def slow(name: str) -> None:
        if name == "b":
            entered.set()
            await release.wait()
        slow_received.append(name)

    async def fast(name: str) -> None:
        fast_received.append(name)

    pipeline.add_listener("drop_started", "bot.slow", slow)
    pipeline.add_listener("drop_started", "bot.fast", fast)
    await pipeline.start()
    try:
        baseline = Observation(frozenset({"a"}), True, 1)
        assert await pipeline.accept(baseline)
        assert await store.pending_callbacks("bot.fast", limit=1) == ()
        for candidate in (
            Observation(frozenset[str](), False, 2),
            Observation(frozenset({"b"}), True, 0),
            Observation(frozenset({"b"}), True, 1),
            Observation(frozenset({"b"}), True),
        ):
            assert not await pipeline.accept(candidate)
            assert pipeline.current is baseline

        assert await asyncio.wait_for(
            pipeline.accept(Observation(frozenset({"a", "b"}), True, 2)),
            timeout=1,
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await pipeline.accept(Observation(frozenset({"a", "b", "c"}), True, 3))
        await _wait_for_pending(store, "bot.fast", 0)
        assert fast_received == ["b", "c"]
        assert slow_received == []
        assert await store.count_pending_callbacks("bot.slow") == 2
        assert pipeline.current is not None and pipeline.current.revision == 3
        checkpoint = await store.read_checkpoint(PollingResource.DROPS)
        assert checkpoint is not None and checkpoint.version == 3
        release.set()
        await _wait_for_pending(store, "bot.slow", 0)
        assert slow_received == ["b", "c"]
    finally:
        release.set()
        await pipeline.close()


def test_observation_rejects_runtime_invalid_flags() -> None:
    with pytest.raises(ConfigurationError, match="completeness"):
        Observation(1, complete=cast(bool, 1))
    with pytest.raises(ConfigurationError, match="revision"):
        Observation(1, complete=True, revision=cast(int, True))


@pytest.mark.asyncio
async def test_initial_emission_and_diff_failure_preserve_checkpoint() -> None:
    store = MemoryEventStore()

    def derive(
        previous: frozenset[str] | None, current: frozenset[str], initial: bool
    ) -> tuple[Emission, ...]:
        if "invalid" in current:
            raise ValueError("invalid snapshot")
        return _changes(previous, current, initial)

    pipeline = StoredEventPipeline(
        PollingResource.DROPS, derive, _SetCodec(), store, emit_initial=True
    )
    received: list[str] = []

    async def listener(name: str) -> None:
        received.append(name)

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    try:
        baseline = Observation(frozenset({"a"}), True, 1)
        assert await pipeline.accept(baseline)
        await _wait_for_pending(store, "bot.alerts", 0)
        assert received == ["a"]
        checkpoint = await store.read_checkpoint(PollingResource.DROPS)
        with pytest.raises(ValueError, match="invalid snapshot"):
            await pipeline.accept(Observation(frozenset({"invalid"}), True, 2))
        assert pipeline.current is baseline
        assert await store.read_checkpoint(PollingResource.DROPS) == checkpoint
        assert await pipeline.accept(Observation(frozenset({"a", "b"}), True, 2))
        await _wait_for_pending(store, "bot.alerts", 0)
        assert received == ["a", "b"]
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_one_emission_never_partially_queues_multiple_listeners() -> None:
    store = MemoryEventStore(max_pending_callbacks=1)
    pipeline = _pipeline(store)
    received: list[str] = []

    async def listener(name: str) -> None:
        received.append(name)

    pipeline.add_listener("drop_started", "bot.first", listener)
    pipeline.add_listener("drop_started", "bot.second", listener)
    await pipeline.start()
    try:
        baseline = Observation(frozenset[str](), True, 1)
        assert await pipeline.accept(baseline)
        with pytest.raises(PendingCallbackLimit):
            await pipeline.accept(Observation(frozenset({"a"}), True, 2))
        assert pipeline.current is baseline
        assert await store.count_pending_callbacks() == 0
        await asyncio.sleep(0)
        assert received == []
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_failed_callback_replays_to_same_subscription_in_new_pipeline() -> None:
    store = MemoryEventStore()
    first = _pipeline(store)
    failed = asyncio.Event()

    async def fail(name: str) -> None:
        failed.set()
        raise RuntimeError("listener failed")

    first.add_listener("drop_started", "bot.alerts", fail)
    await first.start()
    assert await first.accept(Observation(frozenset({"a"}), True, 1))
    assert await first.accept(Observation(frozenset({"a", "b"}), True, 2))
    await asyncio.wait_for(failed.wait(), timeout=1)
    await _wait_for_pending(store, "bot.alerts", 1)
    await first.close()

    received: list[str] = []
    replayed = asyncio.Event()

    async def recover(name: str) -> None:
        received.append(name)
        replayed.set()

    second = _pipeline(store)
    second.add_listener("drop_started", "bot.alerts", recover)
    await second.start()
    try:
        await asyncio.wait_for(replayed.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
        assert received == ["b"]
        assert second.current is not None and second.current.revision == 2
        assert not await second.accept(Observation(frozenset({"a", "b"}), True, 2))
    finally:
        await second.close()


class _AckFailsOnce(MemoryEventStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed_ack = asyncio.Event()

    async def acknowledge(self, subscription_id: str, callback_id: int) -> bool:
        if not self.failed_ack.is_set():
            self.failed_ack.set()
            raise RuntimeError("acknowledgement unavailable")
        return await super().acknowledge(subscription_id, callback_id)


@pytest.mark.asyncio
async def test_success_before_failed_ack_can_run_again_in_new_pipeline() -> None:
    store = _AckFailsOnce()
    calls: list[str] = []

    async def listener(name: str) -> None:
        calls.append(name)

    first = _pipeline(store)
    first.add_listener("drop_started", "bot.alerts", listener)
    await first.start()
    assert await first.accept(Observation(frozenset(), True, 1))
    assert await first.accept(Observation(frozenset({"a"}), True, 2))
    await asyncio.wait_for(store.failed_ack.wait(), timeout=1)
    await first.close()
    assert calls == ["a"]
    assert len(await store.pending_callbacks("bot.alerts", limit=1)) == 1

    second = _pipeline(store)
    second.add_listener("drop_started", "bot.alerts", listener)
    await second.start()
    try:
        await _wait_for_pending(store, "bot.alerts", 0)
        assert calls == ["a", "a"]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_shutdown_during_callback_leaves_delivery_for_replay() -> None:
    store = MemoryEventStore()
    first = _pipeline(store)
    entered = asyncio.Event()

    async def interrupted(name: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    first.add_listener("drop_started", "bot.alerts", interrupted)
    await first.start()
    assert await first.accept(Observation(frozenset(), True, 1))
    assert await first.accept(Observation(frozenset({"a"}), True, 2))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await first.close()
    assert len(await store.pending_callbacks("bot.alerts", limit=1)) == 1

    replayed = asyncio.Event()

    async def recovered(name: str) -> None:
        assert name == "a"
        replayed.set()

    second = _pipeline(store)
    second.add_listener("drop_started", "bot.alerts", recovered)
    await second.start()
    try:
        await asyncio.wait_for(replayed.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_replacing_a_named_listener_replays_its_unacknowledged_call() -> None:
    store = MemoryEventStore()
    pipeline = _pipeline(store)
    entered = asyncio.Event()

    async def interrupted(name: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    pipeline.add_listener("drop_started", "bot.alerts", interrupted)
    await pipeline.start()
    try:
        assert await pipeline.accept(Observation(frozenset(), True, 1))
        assert await pipeline.accept(Observation(frozenset({"a"}), True, 2))
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await pipeline.remove_listener("bot.alerts")
        assert len(await store.pending_callbacks("bot.alerts", limit=1)) == 1

        replayed = asyncio.Event()

        async def replacement(name: str) -> None:
            assert name == "a"
            replayed.set()

        pipeline.add_listener("drop_started", "bot.alerts", replacement)
        await asyncio.wait_for(replayed.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_explicit_retry_resumes_failed_subscription(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = MemoryEventStore()
    pipeline = _pipeline(store)
    attempts = 0
    recovered = asyncio.Event()

    async def listener(name: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient callback failure")
        recovered.set()

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    try:
        with caplog.at_level(logging.ERROR, logger="dankmemer.events"):
            assert await pipeline.accept(Observation(frozenset(), True, 1))
            assert await pipeline.accept(Observation(frozenset({"a"}), True, 2))
            async with asyncio.timeout(2):
                while not any(
                    "event callback failed for bot.alerts" in record.message
                    for record in caplog.records
                ):
                    await asyncio.sleep(0)

        assert attempts == 1
        assert len(await store.pending_callbacks("bot.alerts", limit=1)) == 1
        pipeline.retry_pending("bot.alerts")
        await asyncio.wait_for(recovered.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
        assert attempts == 2
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_listener_can_close_pipeline_and_finish_its_delivery() -> None:
    store = MemoryEventStore()
    pipeline = _pipeline(store)
    finished = asyncio.Event()

    async def listener(name: str) -> None:
        await pipeline.close()
        finished.set()

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    assert await pipeline.accept(Observation(frozenset(), True, 1))
    assert await pipeline.accept(Observation(frozenset({"a"}), True, 2))
    await asyncio.wait_for(finished.wait(), timeout=1)
    await _wait_for_pending(store, "bot.alerts", 0)


@pytest.mark.asyncio
async def test_cancelling_close_caller_does_not_interrupt_cleanup() -> None:
    store = MemoryEventStore()
    pipeline = _pipeline(store)
    entered = asyncio.Event()
    cancelling = asyncio.Event()
    release = asyncio.Event()

    async def listener(name: str) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()
            raise

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    assert await pipeline.accept(Observation(frozenset(), True, 1))
    assert await pipeline.accept(Observation(frozenset({"a"}), True, 2))
    await asyncio.wait_for(entered.wait(), timeout=1)

    closing = asyncio.create_task(pipeline.close())
    await asyncio.wait_for(cancelling.wait(), timeout=1)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    release.set()
    await pipeline.close()
    assert len(await store.pending_callbacks("bot.alerts", limit=1)) == 1


class _CommitFailsOnce(MemoryEventStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

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
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("store unavailable")
        return await super().commit(
            resource,
            expected_version=expected_version,
            payload=payload,
            revision=revision,
            callbacks=callbacks,
            max_pending_callbacks=max_pending_callbacks,
        )


@pytest.mark.asyncio
async def test_failed_commit_keeps_baseline_and_can_retry_same_observation() -> None:
    store = _CommitFailsOnce()
    pipeline = _pipeline(store)
    received = asyncio.Event()

    async def listener(name: str) -> None:
        assert name == "b"
        received.set()

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    try:
        assert await pipeline.accept(Observation(frozenset({"a"}), True, 1))
        store.fail_next = True
        candidate = Observation(frozenset({"a", "b"}), True, 2)
        with pytest.raises(RuntimeError, match="store unavailable"):
            await pipeline.accept(candidate)
        assert pipeline.current is not None and pipeline.current.revision == 1
        assert await store.pending_callbacks("bot.alerts", limit=1) == ()

        assert await pipeline.accept(candidate)
        await asyncio.wait_for(received.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
        assert pipeline.current is candidate
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_full_memory_store_keeps_baseline_until_delivery_drains() -> None:
    store = MemoryEventStore(max_pending_callbacks=1)
    pipeline = _pipeline(store)
    blocked = asyncio.Event()
    release = asyncio.Event()
    delivered_c = asyncio.Event()
    received: list[str] = []

    async def listener(name: str) -> None:
        if name == "b":
            blocked.set()
            await release.wait()
        received.append(name)
        if name == "c":
            delivered_c.set()

    pipeline.add_listener("drop_started", "bot.alerts", listener)
    await pipeline.start()
    try:
        assert await pipeline.accept(Observation(frozenset({"a"}), True, 1))
        assert await pipeline.accept(Observation(frozenset({"a", "b"}), True, 2))
        await asyncio.wait_for(blocked.wait(), timeout=1)

        candidate = Observation(frozenset({"a", "b", "c"}), True, 3)
        with pytest.raises(PendingCallbackLimit):
            await pipeline.accept(candidate)
        assert pipeline.current is not None and pipeline.current.revision == 2

        release.set()
        await _wait_for_pending(store, "bot.alerts", 0)
        assert await pipeline.accept(candidate)
        await asyncio.wait_for(delivered_c.wait(), timeout=1)
        await _wait_for_pending(store, "bot.alerts", 0)
        assert received == ["b", "c"]
        assert pipeline.current is candidate
    finally:
        release.set()
        await pipeline.close()
