from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Sequence
from sqlite3 import Row
from typing import Self, TypeVar, cast

import asqlite

from ._coordination_storage import (
    EVENT_STORE_COORDINATION_TABLE,
    SharedCallbacks,
    SharedCheck,
    SharedUpdate,
)
from ._storage import (
    EVENT_STORE_CHECKPOINTS_TABLE,
    EVENT_STORE_IDENTITY,
    EVENT_STORE_META_TABLE,
    EVENT_STORE_PENDING_CALLBACKS_TABLE,
    EVENT_STORE_PENDING_INDEX,
    CallbackIntent,
    CommitResult,
    PendingCallbackLimit,
    StoredCallback,
    StoredCheckpoint,
    checked_bytes,
    checked_intent,
    checked_positive_int,
    checked_revision,
    checked_subscription_id,
)
from .enums import PollingResource
from .errors import ConfigurationError, LifecycleError

_SCHEMA_VERSION = 2
_Result = TypeVar("_Result")


async def _complete(operation: Awaitable[_Result]) -> _Result:
    # asqlite's worker continues SQL after cancellation. Drain it before reusing
    # the connection or rolling back, including when cancellation is repeated.
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _execute(
    connection: asqlite.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> None:
    async def execute() -> None:
        async with connection.execute(sql, parameters):
            pass

    await _complete(execute())


async def _fetch_optional(
    connection: asqlite.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> Row | None:
    # asqlite types fetchone as Row, but a query with no match returns None.
    return cast(Row | None, await _complete(connection.fetchone(sql, parameters)))


async def _fetch_all(
    connection: asqlite.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[Row]:
    return await _complete(connection.fetchall(sql, parameters))


async def _table_columns(
    connection: asqlite.Connection, table: str
) -> tuple[tuple[str, str, int, int], ...]:
    rows = await _fetch_all(connection, f"PRAGMA table_info({table})")
    return tuple(
        (
            cast(str, row["name"]),
            cast(str, row["type"]).upper(),
            cast(int, row["notnull"]),
            cast(int, row["pk"]),
        )
        for row in rows
    )


async def _validate_schema(
    connection: asqlite.Connection, *, shared: bool = True
) -> None:
    objects = await _fetch_all(
        connection,
        "SELECT name, type, tbl_name FROM sqlite_schema WHERE name IN (?, ?, ?, ?)",
        (
            EVENT_STORE_META_TABLE,
            EVENT_STORE_CHECKPOINTS_TABLE,
            EVENT_STORE_PENDING_CALLBACKS_TABLE,
            EVENT_STORE_PENDING_INDEX,
        ),
    )
    found = {
        (
            cast(str, row["name"]),
            cast(str, row["type"]),
            cast(str, row["tbl_name"]),
        )
        for row in objects
    }
    expected = {
        (EVENT_STORE_META_TABLE, "table", EVENT_STORE_META_TABLE),
        (EVENT_STORE_CHECKPOINTS_TABLE, "table", EVENT_STORE_CHECKPOINTS_TABLE),
        (
            EVENT_STORE_PENDING_CALLBACKS_TABLE,
            "table",
            EVENT_STORE_PENDING_CALLBACKS_TABLE,
        ),
        (EVENT_STORE_PENDING_INDEX, "index", EVENT_STORE_PENDING_CALLBACKS_TABLE),
    }
    if found != expected:
        raise LifecycleError("SQLite event store names conflict with existing objects")

    if await _table_columns(connection, EVENT_STORE_META_TABLE) != (
        ("marker", "TEXT", 1, 1),
        ("schema_version", "INTEGER", 1, 0),
    ):
        raise LifecycleError("SQLite event store metadata has an unexpected schema")

    metadata = await _fetch_all(
        connection,
        f"SELECT marker, schema_version FROM {EVENT_STORE_META_TABLE} LIMIT 2",
    )
    if len(metadata) != 1 or metadata[0]["marker"] != EVENT_STORE_IDENTITY:
        raise LifecycleError("SQLite event store marker does not match this package")
    if metadata[0]["schema_version"] != (_SCHEMA_VERSION if shared else 1):
        raise LifecycleError("unsupported SQLite event store schema version")

    if await _table_columns(connection, EVENT_STORE_CHECKPOINTS_TABLE) != (
        ("resource", "TEXT", 1, 1),
        ("version", "INTEGER", 1, 0),
        ("payload", "BLOB", 1, 0),
        ("revision", "INTEGER", 0, 0),
    ) or await _table_columns(connection, EVENT_STORE_PENDING_CALLBACKS_TABLE) != (
        ("id", "INTEGER", 0, 1),
        ("resource", "TEXT", 1, 0),
        ("checkpoint_version", "INTEGER", 1, 0),
        ("subscription_id", "TEXT", 1, 0),
        ("event", "TEXT", 1, 0),
        ("payload", "BLOB", 1, 0),
    ):
        raise LifecycleError("SQLite event store tables have an unexpected schema")

    index_rows = await _fetch_all(
        connection, f"PRAGMA index_info({EVENT_STORE_PENDING_INDEX})"
    )
    if tuple(cast(str, row["name"]) for row in index_rows) != (
        "subscription_id",
        "id",
    ):
        raise LifecycleError("SQLite event store index has unexpected columns")

    foreign_keys = await _fetch_all(
        connection, f"PRAGMA foreign_key_list({EVENT_STORE_PENDING_CALLBACKS_TABLE})"
    )
    if tuple(
        (
            cast(str, row["table"]),
            cast(str, row["from"]),
            cast(str, row["to"]),
        )
        for row in foreign_keys
    ) != ((EVENT_STORE_CHECKPOINTS_TABLE, "resource", "resource"),):
        raise LifecycleError("SQLite event store foreign key does not match")

    if shared:
        if await _table_columns(connection, EVENT_STORE_COORDINATION_TABLE) != (
            ("marker", "TEXT", 1, 1),
            ("payload", "BLOB", 1, 0),
        ):
            raise LifecycleError("SQLite coordination table has an unexpected schema")

    pending_sql = await _fetch_optional(
        connection,
        "SELECT sql FROM sqlite_schema WHERE name = ?",
        (EVENT_STORE_PENDING_CALLBACKS_TABLE,),
    )
    if (
        pending_sql is None
        or "AUTOINCREMENT" not in cast(str, pending_sql["sql"]).upper()
    ):
        raise LifecycleError("SQLite event store callback IDs may be reused")


class SqliteEventStore:
    """Keep event checkpoints and pending listener calls in a SQLite file.

    Open the store with :meth:`open` and close it when finished. Pending calls
    survive a restart and replay when the same subscription ID is registered.
    A listener may run twice if a process stops after the listener succeeds
    but before its acknowledgement is saved.

    The store implements ``CoordinatedEventStore``. Multiple clients must
    explicitly enable shared coordination to elect pollers and consumers.
    """

    def __init__(self, connection: asqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def open(cls, path: str | os.PathLike[str]) -> Self:
        """Open a file-backed store, creating its tables when needed."""
        location = os.fspath(path)
        if not location or location == ":memory:" or location.startswith("file:"):
            raise ConfigurationError("SQLite event storage requires a file path")
        # asqlite enables PRAGMA foreign_keys=ON when it opens the connection.
        connection = await asqlite.connect(location, timeout=5.0)
        store = cls(connection)
        try:
            await store._initialise()
        except BaseException:
            await _complete(connection.close())
            raise
        return store

    @property
    def durable(self) -> bool:
        """Whether committed data survives closing and reopening this store."""
        return True

    async def __aenter__(self) -> Self:
        if self._closed:
            raise LifecycleError("SQLite event store is closed")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise LifecycleError("SQLite event store is closed")

    async def _initialise(self) -> None:
        connection = self._connection
        async with self._lock:
            try:
                await _execute(connection, "BEGIN IMMEDIATE")
                # Existing names must match our schema, not be repaired in place.
                existing = await _fetch_optional(
                    connection,
                    "SELECT name FROM sqlite_schema WHERE name IN (?, ?, ?, ?) LIMIT 1",
                    (
                        EVENT_STORE_META_TABLE,
                        EVENT_STORE_CHECKPOINTS_TABLE,
                        EVENT_STORE_PENDING_CALLBACKS_TABLE,
                        EVENT_STORE_PENDING_INDEX,
                    ),
                )
                shared_object = await _fetch_optional(
                    connection,
                    "SELECT name FROM sqlite_schema WHERE name = ?",
                    (EVENT_STORE_COORDINATION_TABLE,),
                )
                if existing is None and shared_object is not None:
                    raise LifecycleError(
                        "SQLite event store names conflict with existing objects"
                    )
                if existing is None:
                    await _execute(
                        connection,
                        f"""CREATE TABLE {EVENT_STORE_META_TABLE} (
                            marker TEXT PRIMARY KEY NOT NULL,
                            schema_version INTEGER NOT NULL
                        )""",
                    )
                    await _execute(
                        connection,
                        f"""CREATE TABLE {EVENT_STORE_CHECKPOINTS_TABLE} (
                            resource TEXT PRIMARY KEY NOT NULL,
                            version INTEGER NOT NULL CHECK (version >= 1),
                            payload BLOB NOT NULL,
                            revision INTEGER
                        )""",
                    )
                    await _execute(
                        connection,
                        f"""CREATE TABLE {EVENT_STORE_PENDING_CALLBACKS_TABLE} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            resource TEXT NOT NULL,
                            checkpoint_version INTEGER NOT NULL,
                            subscription_id TEXT NOT NULL,
                            event TEXT NOT NULL,
                            payload BLOB NOT NULL,
                            FOREIGN KEY (resource)
                                REFERENCES {EVENT_STORE_CHECKPOINTS_TABLE}(resource)
                        )""",
                    )
                    await _execute(
                        connection,
                        f"""CREATE INDEX {EVENT_STORE_PENDING_INDEX}
                            ON {EVENT_STORE_PENDING_CALLBACKS_TABLE}
                            (subscription_id, id)""",
                    )
                    await _execute(
                        connection,
                        f"INSERT INTO {EVENT_STORE_META_TABLE} "
                        "(marker, schema_version) VALUES (?, ?)",
                        (EVENT_STORE_IDENTITY, _SCHEMA_VERSION),
                    )
                if existing is not None:
                    await _validate_schema(connection, shared=shared_object is not None)
                metadata = await _fetch_optional(
                    connection,
                    f"SELECT schema_version FROM {EVENT_STORE_META_TABLE} WHERE marker = ?",
                    (EVENT_STORE_IDENTITY,),
                )
                if metadata is not None and metadata["schema_version"] == 1:
                    await _validate_schema(connection, shared=False)
                    await _execute(
                        connection,
                        f"CREATE TABLE {EVENT_STORE_COORDINATION_TABLE} (marker TEXT PRIMARY KEY NOT NULL, payload BLOB NOT NULL)",
                    )
                    await _execute(
                        connection,
                        f"UPDATE {EVENT_STORE_META_TABLE} SET schema_version = ?",
                        (_SCHEMA_VERSION,),
                    )
                elif existing is None:
                    await _execute(
                        connection,
                        f"CREATE TABLE {EVENT_STORE_COORDINATION_TABLE} (marker TEXT PRIMARY KEY NOT NULL, payload BLOB NOT NULL)",
                    )
                await _validate_schema(connection)
                await _complete(connection.commit())
            except BaseException:
                await _complete(connection.rollback())
                raise

    async def read_checkpoint(
        self, resource: PollingResource
    ) -> StoredCheckpoint | None:
        """Return the most recent checkpoint for a resource, if any."""
        async with self._lock:
            self._require_open()
            row = await _fetch_optional(
                self._connection,
                "SELECT version, payload, revision "
                f"FROM {EVENT_STORE_CHECKPOINTS_TABLE} WHERE resource = ?",
                (resource.value,),
            )
        if row is None:
            return None
        return _checkpoint(resource, row)

    async def commit(
        self,
        resource: PollingResource,
        *,
        expected_version: int | None,
        payload: bytes,
        revision: int | None,
        callbacks: Sequence[CallbackIntent] | SharedCallbacks,
        max_pending_callbacks: int | None = None,
    ) -> CommitResult | None:
        """Save a checkpoint and its pending calls in one transaction.

        Return ``None`` if another store has already advanced the resource.
        """
        if expected_version is not None:
            checked_positive_int(expected_version, "expected_version")
        checked_bytes(payload, "checkpoint payload")
        checked_revision(revision)
        intents = (
            ()
            if callable(callbacks)
            else tuple(checked_intent(intent) for intent in callbacks)
        )
        if max_pending_callbacks is not None:
            checked_positive_int(max_pending_callbacks, "max_pending_callbacks")

        async with self._lock:
            self._require_open()
            connection = self._connection
            try:
                # Lock writers before reading the version, including other processes.
                await _execute(connection, "BEGIN IMMEDIATE")
                if callable(callbacks):
                    state, now = await self._shared_state(connection)
                    intents = tuple(
                        checked_intent(intent) for intent in callbacks(state, now)
                    )
                row = await _fetch_optional(
                    connection,
                    f"SELECT version FROM {EVENT_STORE_CHECKPOINTS_TABLE} "
                    "WHERE resource = ?",
                    (resource.value,),
                )
                current_version = None if row is None else cast(int, row["version"])
                if current_version != expected_version:
                    await _complete(connection.rollback())
                    return None

                if intents and max_pending_callbacks is not None:
                    count = await _fetch_optional(
                        connection,
                        f"SELECT COUNT(*) AS total FROM {EVENT_STORE_PENDING_CALLBACKS_TABLE}",
                    )
                    assert count is not None
                    if cast(int, count["total"]) + len(intents) > max_pending_callbacks:
                        raise PendingCallbackLimit(
                            "event store pending callback limit reached"
                        )

                next_version = 1 if current_version is None else current_version + 1
                await _execute(
                    connection,
                    f"""INSERT INTO {EVENT_STORE_CHECKPOINTS_TABLE}
                       (resource, version, payload, revision) VALUES (?, ?, ?, ?)
                       ON CONFLICT(resource) DO UPDATE SET
                           version = excluded.version,
                           payload = excluded.payload,
                           revision = excluded.revision""",
                    (resource.value, next_version, payload, revision),
                )

                records: list[StoredCallback] = []
                for intent in intents:
                    callback_row = await _fetch_optional(
                        connection,
                        f"""INSERT INTO {EVENT_STORE_PENDING_CALLBACKS_TABLE}
                           (resource, checkpoint_version, subscription_id,
                            event, payload) VALUES (?, ?, ?, ?, ?) RETURNING id""",
                        (
                            resource.value,
                            next_version,
                            intent.subscription_id,
                            intent.event,
                            intent.payload,
                        ),
                    )
                    assert callback_row is not None
                    records.append(
                        StoredCallback(
                            cast(int, callback_row["id"]),
                            resource,
                            next_version,
                            intent.subscription_id,
                            intent.event,
                            intent.payload,
                        )
                    )
                await _complete(connection.commit())
            except BaseException:
                await _complete(connection.rollback())
                raise

        checkpoint = StoredCheckpoint(resource, next_version, payload, revision)
        return CommitResult(checkpoint, tuple(records))

    async def pending_callbacks(
        self, subscription_id: str, *, limit: int
    ) -> tuple[StoredCallback, ...]:
        """Read unacknowledged calls for a subscription in commit order."""
        checked_subscription_id(subscription_id)
        checked_positive_int(limit, "limit")
        async with self._lock:
            self._require_open()
            rows = await _fetch_all(
                self._connection,
                f"""SELECT id, resource, checkpoint_version, event, payload
                   FROM {EVENT_STORE_PENDING_CALLBACKS_TABLE}
                   WHERE subscription_id = ? ORDER BY id LIMIT ?""",
                (subscription_id, limit),
            )
        return tuple(
            StoredCallback(
                cast(int, row["id"]),
                PollingResource(cast(str, row["resource"])),
                cast(int, row["checkpoint_version"]),
                subscription_id,
                cast(str, row["event"]),
                cast(bytes, row["payload"]),
            )
            for row in rows
        )

    async def acknowledge(self, subscription_id: str, callback_id: int) -> bool:
        """Remove one delivered call belonging to the given subscription."""
        return await self._acknowledge(subscription_id, callback_id)

    async def acknowledge_coordinated(
        self, subscription_id: str, callback_id: int, check: SharedCheck
    ) -> bool:
        """Check shared ownership and remove a delivered call atomically."""
        return await self._acknowledge(subscription_id, callback_id, check)

    async def _acknowledge(
        self, subscription_id: str, callback_id: int, check: SharedCheck | None = None
    ) -> bool:
        checked_subscription_id(subscription_id)
        checked_positive_int(callback_id, "callback_id")
        async with self._lock:
            self._require_open()
            connection = self._connection
            try:
                if check is not None:
                    await _execute(connection, "BEGIN IMMEDIATE")
                    state, now = await self._shared_state(connection)
                    check(state, now)
                row = await _fetch_optional(
                    connection,
                    f"DELETE FROM {EVENT_STORE_PENDING_CALLBACKS_TABLE} WHERE subscription_id = ? AND id = ? RETURNING id",
                    (subscription_id, callback_id),
                )
                if check is not None:
                    await _complete(connection.commit())
            except BaseException:
                if check is not None:
                    await _complete(connection.rollback())
                raise
        return row is not None

    async def _shared_state(
        self, connection: asqlite.Connection
    ) -> tuple[bytes | None, float]:
        rows = await _fetch_all(
            connection,
            f"SELECT marker, payload FROM {EVENT_STORE_COORDINATION_TABLE} LIMIT 2",
        )
        if len(rows) > 1 or (rows and rows[0]["marker"] != EVENT_STORE_IDENTITY):
            raise LifecycleError("SQLite coordination state marker does not match")
        row = await _fetch_optional(
            connection, "SELECT (julianday('now') - 2440587.5) * 86400.0 AS now"
        )
        assert row is not None
        return (
            cast(bytes, rows[0]["payload"]) if rows else None,
            cast(float, row["now"]),
        )

    async def coordinate(self, update: SharedUpdate[_Result]) -> _Result:
        """Update shared state atomically using the database's UTC clock."""
        async with self._lock:
            self._require_open()
            connection = self._connection
            try:
                await _execute(connection, "BEGIN IMMEDIATE")
                previous, now = await self._shared_state(connection)
                payload, result = update(previous, now)
                checked_bytes(payload, "coordination payload")
                await _execute(
                    connection,
                    f"INSERT INTO {EVENT_STORE_COORDINATION_TABLE} (marker, payload) VALUES (?, ?) ON CONFLICT(marker) DO UPDATE SET payload = excluded.payload",
                    (EVENT_STORE_IDENTITY, payload),
                )
                await _complete(connection.commit())
            except BaseException:
                await _complete(connection.rollback())
                raise
        return result

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
        """Derive listener calls under the shared lock, then commit them."""
        return await self.commit(
            resource,
            expected_version=expected_version,
            payload=payload,
            revision=revision,
            callbacks=callbacks,
            max_pending_callbacks=max_pending_callbacks,
        )

    async def close(self) -> None:
        """Close the SQLite connection without discarding pending calls."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await _complete(self._connection.close())

    async def count_pending_callbacks(self, subscription_id: str | None = None) -> int:
        """Count saved calls, optionally selecting one subscription."""
        if subscription_id is not None:
            checked_subscription_id(subscription_id)
        sql = f"SELECT COUNT(*) AS total FROM {EVENT_STORE_PENDING_CALLBACKS_TABLE}"
        parameters: tuple[object, ...] = ()
        if subscription_id is not None:
            sql += " WHERE subscription_id = ?"
            parameters = (subscription_id,)
        async with self._lock:
            self._require_open()
            row = await _fetch_optional(self._connection, sql, parameters)
        assert row is not None
        return cast(int, row["total"])


def _checkpoint(resource: PollingResource, row: Row) -> StoredCheckpoint:
    return StoredCheckpoint(
        resource,
        cast(int, row["version"]),
        cast(bytes, row["payload"]),
        cast(int | None, row["revision"]),
    )
