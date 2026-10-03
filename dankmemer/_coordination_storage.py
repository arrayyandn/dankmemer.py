from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar, runtime_checkable

from ._storage import CallbackIntent, CommitResult, EventStore
from .enums import PollingResource

EVENT_STORE_COORDINATION_TABLE = "dankmemer_py_events_coordination_010926"

_T = TypeVar("_T")
SharedUpdate = Callable[[bytes | None, float], tuple[bytes, _T]]
SharedCallbacks = Callable[[bytes | None, float], Sequence[CallbackIntent]]
SharedCheck = Callable[[bytes | None, float], None]


@runtime_checkable
class CoordinatedEventStore(EventStore, Protocol):
    """An event store with atomic operations for shared clients.

    The coordinate operation locks shared state, reads the database's UTC
    clock, calls the synchronous update, and saves its bytes in one transaction.
    Updates must not await, perform external actions, or call the store again.
    Invoke them on the calling event loop, including when SQL runs in a worker
    thread.
    An exception rolls back the transaction. An unused store supplies ``None``;
    otherwise it supplies the exact bytes saved by the previous update.

    Coordinated commits and acknowledgements must hold that same lock while
    checking ownership and changing event records. The callbacks supplied to
    these methods validate ownership; their exceptions leave all data intact.
    The UTC time must come from the database after acquiring the shared lock,
    rather than the caller's monotonic clock. Built-in SQLite and PostgreSQL
    stores implement this protocol. It does not require inheritance.
    """

    async def coordinate(self, update: SharedUpdate[_T]) -> _T:
        """Update shared coordination state under the store's transaction lock.

        Call ``update(previous, utc_seconds)`` on the caller's event loop.
        ``previous`` is the last saved bytes, or ``None`` for an unused store.
        Read ``utc_seconds`` from the database after acquiring the lock.
        The update returns ``(new_bytes, result)``: save the bytes atomically
        and return the result. An exception rolls back the update.

        The synchronous update must not await, perform external actions, or
        re-enter the store. Use the same lock for coordinated event commits
        and acknowledgements so ownership cannot change during their checks.
        """
        ...

    async def commit_coordinated(
        self,
        resource: PollingResource,
        *,
        expected_version: int | None,
        payload: bytes,
        revision: int | None,
        callbacks: SharedCallbacks,
        max_pending_callbacks: int | None = None,
    ) -> CommitResult | None:
        """Commit event records while checking current shared ownership.

        Under the coordination lock, call ``callbacks(previous, utc_seconds)``
        on the caller's event loop to obtain the callback intents. Supply the
        same bytes and database time used by :meth:`coordinate`. The function
        validates ownership and must have no external side effects.

        Apply :meth:`EventStore.commit` version, ordering, and capacity rules
        in that same transaction. Ownership-check or storage exceptions leave
        checkpoints, pending calls, and coordination state unchanged. Return
        ``None`` for a version mismatch, or the successful commit result.
        """
        ...

    async def acknowledge_coordinated(
        self, subscription_id: str, callback_id: int, check: SharedCheck
    ) -> bool:
        """Acknowledge a call only while its shared ownership is valid.

        Under the coordination lock, call ``check(previous, utc_seconds)``
        on the caller's event loop using the saved bytes and database UTC
        time. The synchronous check must not perform external actions or
        re-enter the store. If it raises, roll back without removing the call.

        Otherwise apply :meth:`EventStore.acknowledge` in the same transaction:
        return ``True`` when removed and ``False`` for an unknown, acknowledged,
        or differently owned subscription ID. Do not change the checkpoint.
        """
        ...
