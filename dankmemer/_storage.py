from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice
from typing import Protocol, runtime_checkable

from .enums import PollingResource
from .errors import ConfigurationError

# 010926 identifies this package's database objects; schema versions change separately.
EVENT_STORE_META_TABLE = "dankmemer_py_events_meta_010926"
EVENT_STORE_CHECKPOINTS_TABLE = "dankmemer_py_events_checkpoints_010926"
EVENT_STORE_PENDING_CALLBACKS_TABLE = "dankmemer_py_events_pending_callbacks_010926"
EVENT_STORE_PENDING_INDEX = "dankmemer_py_events_pending_by_subscription_010926"
EVENT_STORE_PENDING_SEQUENCE = "dankmemer_py_events_pending_ids_010926"
EVENT_STORE_IDENTITY = "dankmemer.py/events/010926"


def checked_subscription_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("subscription_id must be a nonempty string")
    return value


def checked_event_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError("callback event must be a nonempty string")
    return value


def checked_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def checked_bytes(value: object, name: str) -> bytes:
    if type(value) is not bytes:
        raise ConfigurationError(f"{name} must be bytes")
    return value


def checked_revision(value: object) -> int | None:
    if value is not None and type(value) is not int:
        raise ConfigurationError("checkpoint revision must be an integer")
    return value


def checked_intent(value: object) -> CallbackIntent:
    if not isinstance(value, CallbackIntent):
        raise ConfigurationError("callbacks must contain CallbackIntent values")
    return value


@dataclass(frozen=True, slots=True)
class CallbackIntent:
    """Encoded data for one future listener call.

    ``subscription_id`` must be stable across restarts for durable replay.
    ``payload`` is encoded outside the store; no Python callable is stored.
    """

    subscription_id: str
    event: str
    payload: bytes

    def __post_init__(self) -> None:
        checked_subscription_id(self.subscription_id)
        checked_event_name(self.event)
        checked_bytes(self.payload, "callback payload")


@dataclass(frozen=True, slots=True)
class StoredCheckpoint:
    """An encoded resource baseline and its local version number."""

    resource: PollingResource
    version: int
    payload: bytes
    revision: int | None


@dataclass(frozen=True, slots=True)
class StoredCallback:
    """A listener call awaiting acknowledgement by its named subscription."""

    id: int
    resource: PollingResource
    checkpoint_version: int
    subscription_id: str
    event: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class CommitResult:
    """The checkpoint and callback records saved by one successful commit."""

    checkpoint: StoredCheckpoint
    callbacks: tuple[StoredCallback, ...]


@runtime_checkable
class EventStore(Protocol):
    """Save a checkpoint and its pending callbacks in one atomic commit.

    A commit with the wrong ``expected_version`` must make no changes and
    return ``None``. A configured capacity limit must raise :class:`BufferError`
    without changing the checkpoint or callbacks. Pending callbacks remain
    available until acknowledged.
    A store may report ``durable=True`` only if committed data survives a
    process restart. Delivery is at least once: a callback can run again if
    the process stops after it succeeds but before its acknowledgement saves.
    ``max_pending_callbacks`` bounds the whole store, including inactive
    subscriptions, and must be checked inside the same atomic commit.
    ``count_pending_callbacks(None)`` counts all saved calls; an ID selects
    one subscription. Running callbacks remain pending until acknowledged.
    """

    @property
    def durable(self) -> bool:
        """Whether successfully committed records survive a process restart.

        Return ``False`` for process-local storage. The client requires
        ``True`` when configured for durable event delivery.
        """
        ...

    async def read_checkpoint(
        self, resource: PollingResource
    ) -> StoredCheckpoint | None:
        """Return the last committed checkpoint for ``resource``, or ``None``.

        Preserve its payload bytes, revision, and positive version exactly.
        Reading does not change the checkpoint or acknowledge pending calls.
        A storage failure raises an exception without deleting saved records.
        """
        ...

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
        """Atomically save a checkpoint and the supplied callback intents.

        ``expected_version=None`` requires that no checkpoint exists.
        Otherwise it must match the current version. A mismatch returns
        ``None`` without changing any records. A successful commit creates
        version 1 or increments the previous version by one, preserving
        ``payload`` and ``revision`` without interpreting them.

        Return the saved checkpoint and callbacks in a :class:`CommitResult`.
        Give each callback a unique positive ID within the store, associate
        it with this checkpoint version, and preserve the supplied order.
        Calls for each subscription must replay in commit order, including
        calls from different resources.

        ``max_pending_callbacks`` optionally limits all unacknowledged calls
        in the store, including running calls and inactive subscriptions.
        Check capacity inside the commit. Exceeding capacity raises
        :class:`BufferError`; any exception leaves every record unchanged.
        """
        ...

    async def pending_callbacks(
        self, subscription_id: str, *, limit: int
    ) -> tuple[StoredCallback, ...]:
        """Read up to ``limit`` pending calls for one subscription.

        ``limit`` is a positive integer. Return calls in commit order, or
        an empty tuple if none remain. Reading neither claims nor removes
        calls; a running callback remains pending until acknowledged.
        """
        ...

    async def acknowledge(self, subscription_id: str, callback_id: int) -> bool:
        """Remove one successfully delivered call belonging to the subscription.

        Return ``True`` when removed. Return ``False`` if the ID is unknown,
        already acknowledged, or belongs to another subscription. Leave the
        checkpoint and other calls unchanged. Failed acknowledgements must
        keep the call available for replay.
        """
        ...

    async def count_pending_callbacks(self, subscription_id: str | None = None) -> int:
        """Count unacknowledged calls, including those currently running.

        ``None`` counts all subscriptions, including inactive ones. An ID
        selects only that subscription. This operation does not change records.
        """
        ...


class PendingCallbackLimit(BufferError):
    """A store cannot accept more pending callbacks without losing data."""


class MemoryEventStore:
    """Process-local event state for best-effort delivery.

    The data disappears with this instance. Use a persistent store for
    callback replay after a restart. ``max_pending_callbacks`` bounds the
    memory used by unacknowledged listener calls; its default matches the
    client's delivery limit. It is not an API quota.
    """

    def __init__(self, *, max_pending_callbacks: int = 1_000) -> None:
        self._max_pending_callbacks = checked_positive_int(
            max_pending_callbacks, "max_pending_callbacks"
        )
        self._checkpoints: dict[PollingResource, StoredCheckpoint] = {}
        self._callbacks: dict[int, StoredCallback] = {}
        self._next_callback_id = 1
        self._lock = asyncio.Lock()

    @property
    def durable(self) -> bool:
        """Whether this store retains state across process restarts."""
        return False

    @property
    def pending_count(self) -> int:
        """Number of unacknowledged calls, including running callbacks."""
        return len(self._callbacks)

    def _discard_pending(self, subscription_id: str, *, keep_id: int | None) -> None:
        # Memory mutations never await, so listener removal cannot split a commit.
        self._callbacks = {
            callback_id: record
            for callback_id, record in self._callbacks.items()
            if record.subscription_id != subscription_id or callback_id == keep_id
        }

    def _clear(self, *, keep_ids: frozenset[int]) -> None:
        self._checkpoints.clear()
        self._callbacks = {
            callback_id: record
            for callback_id, record in self._callbacks.items()
            if callback_id in keep_ids
        }

    async def read_checkpoint(
        self, resource: PollingResource
    ) -> StoredCheckpoint | None:
        """Return the latest checkpoint for ``resource``, if any."""
        async with self._lock:
            return self._checkpoints.get(resource)

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
        """Save a baseline and its callbacks if its version has not changed."""
        if expected_version is not None:
            checked_positive_int(expected_version, "expected_version")
        checked_bytes(payload, "checkpoint payload")
        checked_revision(revision)
        intents = tuple(checked_intent(intent) for intent in callbacks)
        capacity = self._max_pending_callbacks
        if max_pending_callbacks is not None:
            capacity = min(
                capacity,
                checked_positive_int(max_pending_callbacks, "max_pending_callbacks"),
            )

        async with self._lock:
            previous = self._checkpoints.get(resource)
            current_version = None if previous is None else previous.version
            if current_version != expected_version:
                return None
            if intents and len(self._callbacks) + len(intents) > capacity:
                raise PendingCallbackLimit("event store pending callback limit reached")

            next_version = 1 if current_version is None else current_version + 1
            checkpoint = StoredCheckpoint(resource, next_version, payload, revision)
            records = tuple(
                StoredCallback(
                    self._next_callback_id + index,
                    resource,
                    next_version,
                    intent.subscription_id,
                    intent.event,
                    intent.payload,
                )
                for index, intent in enumerate(intents)
            )
            self._checkpoints[resource] = checkpoint
            self._callbacks.update((record.id, record) for record in records)
            self._next_callback_id += len(records)
            return CommitResult(checkpoint, records)

    async def pending_callbacks(
        self, subscription_id: str, *, limit: int
    ) -> tuple[StoredCallback, ...]:
        """Read up to ``limit`` unacknowledged callbacks in commit order."""
        checked_subscription_id(subscription_id)
        checked_positive_int(limit, "limit")
        async with self._lock:
            return tuple(
                islice(
                    (
                        record
                        for record in self._callbacks.values()
                        if record.subscription_id == subscription_id
                    ),
                    limit,
                )
            )

    async def acknowledge(self, subscription_id: str, callback_id: int) -> bool:
        """Remove a callback after successful delivery.

        Return ``False`` when the callback is unknown, already acknowledged,
        or belongs to another subscription.
        """
        checked_subscription_id(subscription_id)
        checked_positive_int(callback_id, "callback_id")
        async with self._lock:
            record = self._callbacks.get(callback_id)
            if record is None or record.subscription_id != subscription_id:
                return False
            del self._callbacks[callback_id]
            return True

    async def count_pending_callbacks(self, subscription_id: str | None = None) -> int:
        """Count saved calls, optionally selecting one subscription."""
        if subscription_id is not None:
            checked_subscription_id(subscription_id)
        async with self._lock:
            return sum(
                subscription_id is None or record.subscription_id == subscription_id
                for record in self._callbacks.values()
            )
