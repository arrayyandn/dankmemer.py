import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from store_helpers import database, open_store

from dankmemer._storage import (
    CallbackIntent,
    CommitResult,
    EventStore,
    MemoryEventStore,
    PendingCallbackLimit,
)
from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError


@pytest_asyncio.fixture(params=("memory", "sqlite", "postgres"))
async def event_stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[EventStore, EventStore]]:
    backend = cast(str, request.param)
    if backend == "memory":
        store = MemoryEventStore()
        yield store, store
        return
    async with database(backend, tmp_path) as (left, location, schema):
        async with open_store(backend, location, schema) as right:
            yield left, right


@pytest.mark.asyncio
async def test_checkpoint_and_pending_callbacks_commit_as_one_unit(
    event_stores: tuple[EventStore, EventStore],
) -> None:
    store, reader = event_stores
    intent = CallbackIntent("bot.alerts", "drop_started", b'{"id":"a"}')

    saved = await store.commit(
        PollingResource.DROPS,
        expected_version=None,
        payload=b'{"active":["a"]}',
        revision=1,
        callbacks=(intent,),
    )
    assert saved is not None
    assert saved.checkpoint.version == 1
    assert saved.checkpoint.revision == 1
    assert await reader.read_checkpoint(PollingResource.DROPS) == saved.checkpoint
    assert await store.read_checkpoint(PollingResource.BLOGS) is None
    assert await store.pending_callbacks("bot.alerts", limit=10) == saved.callbacks

    conflict = await store.commit(
        PollingResource.DROPS,
        expected_version=None,
        payload=b'{"active":[]}',
        revision=2,
        callbacks=(CallbackIntent("bot.alerts", "drop_ended", b"{}"),),
    )
    assert conflict is None
    assert await store.read_checkpoint(PollingResource.DROPS) == saved.checkpoint
    assert await store.pending_callbacks("bot.alerts", limit=10) == saved.callbacks

    callback = saved.callbacks[0]
    assert not await store.acknowledge("another.bot", callback.id)
    assert await store.pending_callbacks("bot.alerts", limit=10) == saved.callbacks
    assert await store.acknowledge("bot.alerts", callback.id)
    assert not await store.acknowledge("bot.alerts", callback.id)
    assert await store.pending_callbacks("bot.alerts", limit=10) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_version", [None, 1])
async def test_competing_commits_leave_one_complete_checkpoint(
    event_stores: tuple[EventStore, EventStore], expected_version: int | None
) -> None:
    left, right = event_stores
    if expected_version is not None:
        assert (
            await left.commit(
                PollingResource.BLOGS,
                expected_version=None,
                payload=b"baseline",
                revision=0,
                callbacks=(),
            )
            is not None
        )

    async def commit(store: EventStore, value: bytes) -> CommitResult | None:
        return await store.commit(
            PollingResource.BLOGS,
            expected_version=expected_version,
            payload=value,
            revision=None,
            callbacks=(CallbackIntent("bot.news", "blog_posted", value),),
        )

    results = await asyncio.gather(commit(left, b"one"), commit(right, b"two"))
    assert sum(result is not None for result in results) == 1
    checkpoint = await right.read_checkpoint(PollingResource.BLOGS)
    pending = await right.pending_callbacks("bot.news", limit=10)
    assert checkpoint is not None
    assert checkpoint.version == (1 if expected_version is None else 2)
    assert len(pending) == 1
    assert pending[0].payload == checkpoint.payload


@pytest.mark.asyncio
async def test_pending_callbacks_are_ordered_and_limited(
    event_stores: tuple[EventStore, EventStore],
) -> None:
    store, reader = event_stores
    first = await store.commit(
        PollingResource.DROPS,
        expected_version=None,
        payload=b"first",
        revision=None,
        callbacks=(
            CallbackIntent("bot.alerts", "drop_started", b"a"),
            CallbackIntent("bot.news", "drop_started", b"a"),
        ),
    )
    assert first is not None
    second = await store.commit(
        PollingResource.DROPS,
        expected_version=first.checkpoint.version,
        payload=b"second",
        revision=None,
        callbacks=(CallbackIntent("bot.alerts", "drop_started", b"b"),),
    )
    assert second is not None
    assert await reader.pending_callbacks("bot.alerts", limit=1) == (
        first.callbacks[0],
    )
    assert await reader.pending_callbacks("bot.alerts", limit=2) == (
        first.callbacks[0],
        second.callbacks[0],
    )


@pytest.mark.asyncio
async def test_capacity_rejects_entire_commit(
    event_stores: tuple[EventStore, EventStore],
) -> None:
    store, reader = event_stores
    baseline = await store.commit(
        PollingResource.DROPS,
        expected_version=None,
        payload=b"baseline",
        revision=1,
        callbacks=(),
    )
    assert baseline is not None
    with pytest.raises(PendingCallbackLimit):
        await store.commit(
            PollingResource.DROPS,
            expected_version=1,
            payload=b"changed",
            revision=2,
            callbacks=(
                CallbackIntent("bot.first", "drop_started", b"a"),
                CallbackIntent("bot.second", "drop_started", b"a"),
            ),
            max_pending_callbacks=1,
        )
    assert await reader.read_checkpoint(PollingResource.DROPS) == baseline.checkpoint
    assert await reader.count_pending_callbacks() == 0


@pytest.mark.asyncio
async def test_capacity_counts_other_resources_and_inactive_subscriptions(
    event_stores: tuple[EventStore, EventStore],
) -> None:
    left, right = event_stores
    results = await asyncio.gather(
        left.commit(
            PollingResource.BLOGS,
            expected_version=None,
            payload=b"first",
            revision=None,
            callbacks=(CallbackIntent("old.listener", "blog_published", b"old"),),
            max_pending_callbacks=1,
        ),
        right.commit(
            PollingResource.LOTTERY,
            expected_version=None,
            payload=b"second",
            revision=None,
            callbacks=(CallbackIntent("new.listener", "lottery_result", b"new"),),
            max_pending_callbacks=1,
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, CommitResult) for result in results) == 1
    assert sum(isinstance(result, PendingCallbackLimit) for result in results) == 1
    accepted = next(result for result in results if isinstance(result, CommitResult))
    callback = accepted.callbacks[0]
    assert await right.count_pending_callbacks() == 1
    assert await right.count_pending_callbacks(callback.subscription_id) == 1
    assert await right.count_pending_callbacks("missing") == 0
    checkpoints = await asyncio.gather(
        right.read_checkpoint(PollingResource.BLOGS),
        right.read_checkpoint(PollingResource.LOTTERY),
    )
    assert sum(checkpoint is not None for checkpoint in checkpoints) == 1
    assert await right.acknowledge(callback.subscription_id, callback.id)
    assert await left.count_pending_callbacks() == 0
    assert (
        await left.commit(
            PollingResource.DROPS,
            expected_version=None,
            payload=b"after acknowledgement",
            revision=None,
            callbacks=(CallbackIntent("another.listener", "drop_started", b"new"),),
            max_pending_callbacks=1,
        )
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_checkpoint_callbacks_and_acknowledgements_survive_reopen(
    backend: str, tmp_path: Path
) -> None:
    async with database(backend, tmp_path) as (_, location, schema):
        async with open_store(backend, location, schema) as store:
            assert store.durable
            first = await store.commit(
                PollingResource.DROPS,
                expected_version=None,
                payload=b"first",
                revision=10,
                callbacks=(
                    CallbackIntent("bot.drops", "drop_started", b"a"),
                    CallbackIntent("bot.other", "drop_started", b"a"),
                    CallbackIntent("bot.drops", "drop_started", b"b"),
                ),
            )
            assert first is not None
        async with open_store(backend, location, schema) as reopened:
            assert (
                await reopened.read_checkpoint(PollingResource.DROPS)
                == first.checkpoint
            )
            assert await reopened.pending_callbacks("bot.drops", limit=1) == (
                first.callbacks[0],
            )
            assert not await reopened.acknowledge(
                "another.listener", first.callbacks[0].id
            )
            assert await reopened.acknowledge("bot.drops", first.callbacks[0].id)
            assert not await reopened.acknowledge("bot.drops", first.callbacks[0].id)
            second = await reopened.commit(
                PollingResource.DROPS,
                expected_version=1,
                payload=b"second",
                revision=None,
                callbacks=(CallbackIntent("bot.drops", "drop_started", b"c"),),
            )
            assert second is not None
            assert second.callbacks[0].id > first.callbacks[-1].id
        async with open_store(backend, location, schema) as reopened:
            assert (
                await reopened.read_checkpoint(PollingResource.DROPS)
                == second.checkpoint
            )
            assert await reopened.pending_callbacks("bot.drops", limit=10) == (
                first.callbacks[2],
                second.callbacks[0],
            )


def test_invalid_store_arguments_are_rejected() -> None:
    with pytest.raises(ConfigurationError, match="subscription_id"):
        CallbackIntent("", "drop_started", b"x")
    with pytest.raises(ConfigurationError, match="payload"):
        CallbackIntent("bot.alerts", "drop_started", cast(bytes, "x"))
