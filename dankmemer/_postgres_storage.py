from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Self, TypeVar, cast

import asyncpg
from asyncpg.pool import PoolConnectionProxy

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
    EVENT_STORE_PENDING_SEQUENCE,
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
_OBJECTS = {
    EVENT_STORE_COORDINATION_TABLE: "r",
    EVENT_STORE_META_TABLE: "r",
    EVENT_STORE_CHECKPOINTS_TABLE: "r",
    EVENT_STORE_PENDING_CALLBACKS_TABLE: "r",
    EVENT_STORE_PENDING_INDEX: "i",
    EVENT_STORE_PENDING_SEQUENCE: "S",
}


def _qualified(schema: str, name: str) -> str:
    def quoted(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    return f"{quoted(schema)}.{quoted(name)}"


def _checked_schema(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigurationError("schema must be a nonempty PostgreSQL schema name")
    return value


async def _validate_schema(
    connection: PoolConnectionProxy, schema: str, *, shared: bool = True
) -> None:
    expected_objects = {
        name: kind
        for name, kind in _OBJECTS.items()
        if shared or name != EVENT_STORE_COORDINATION_TABLE
    }
    table_names = [
        EVENT_STORE_META_TABLE,
        EVENT_STORE_CHECKPOINTS_TABLE,
        EVENT_STORE_PENDING_CALLBACKS_TABLE,
    ]
    if shared:
        table_names.append(EVENT_STORE_COORDINATION_TABLE)
    objects = await connection.fetch(
        """SELECT c.relname, c.relkind::text AS relkind
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = $1 AND c.relname = ANY($2::text[])""",
        schema,
        list(_OBJECTS),
    )
    if {row["relname"]: row["relkind"] for row in objects} != expected_objects:
        raise LifecycleError(
            "PostgreSQL event store names conflict with existing objects"
        )

    meta = _qualified(schema, EVENT_STORE_META_TABLE)
    columns = await connection.fetch(
        """SELECT c.relname AS table_name, a.attname, a.atttypid::regtype::text
                  AS type_name, a.attnotnull
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
           WHERE n.nspname = $1 AND c.relname = ANY($2::text[])
                 AND a.attnum > 0 AND NOT a.attisdropped
           ORDER BY c.relname, a.attnum""",
        schema,
        table_names,
    )
    found_columns: dict[str, tuple[tuple[str, str, bool], ...]] = {
        name: tuple(
            (row["attname"], row["type_name"], row["attnotnull"])
            for row in columns
            if row["table_name"] == name
        )
        for name in table_names
    }
    expected_columns = {
        EVENT_STORE_META_TABLE: (
            ("marker", "text", True),
            ("schema_version", "integer", True),
        ),
        EVENT_STORE_CHECKPOINTS_TABLE: (
            ("resource", "text", True),
            ("version", "bigint", True),
            ("payload", "bytea", True),
            ("revision", "bigint", False),
        ),
        EVENT_STORE_PENDING_CALLBACKS_TABLE: (
            ("id", "bigint", True),
            ("resource", "text", True),
            ("checkpoint_version", "bigint", True),
            ("subscription_id", "text", True),
            ("event", "text", True),
            ("payload", "bytea", True),
        ),
    }
    if shared:
        expected_columns[EVENT_STORE_COORDINATION_TABLE] = (
            ("marker", "text", True),
            ("payload", "bytea", True),
        )
    if found_columns != expected_columns:
        raise LifecycleError("PostgreSQL event store tables have an unexpected schema")

    metadata = await connection.fetch(
        f"SELECT marker, schema_version FROM {meta} LIMIT 2"
    )
    if len(metadata) != 1 or metadata[0]["marker"] != EVENT_STORE_IDENTITY:
        raise LifecycleError(
            "PostgreSQL event store marker does not match this package"
        )
    if metadata[0]["schema_version"] != (_SCHEMA_VERSION if shared else 1):
        raise LifecycleError("unsupported PostgreSQL event store schema version")

    constraints = await connection.fetch(
        """SELECT t.relname AS table_name, c.contype::text AS contype,
                  ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY
                        AS k(num, pos) JOIN pg_catalog.pg_attribute a
                        ON a.attrelid = t.oid AND a.attnum = k.num
                        ORDER BY k.pos) AS local_columns,
                  ref.relname AS referenced_table, refn.nspname AS referenced_schema,
                  ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY
                        AS k(num, pos) JOIN pg_catalog.pg_attribute a
                        ON a.attrelid = ref.oid AND a.attnum = k.num
                        ORDER BY k.pos) AS referenced_columns
           FROM pg_catalog.pg_constraint c
           JOIN pg_catalog.pg_class t ON t.oid = c.conrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
           LEFT JOIN pg_catalog.pg_class ref ON ref.oid = c.confrelid
           LEFT JOIN pg_catalog.pg_namespace refn ON refn.oid = ref.relnamespace
           WHERE n.nspname = $1 AND t.relname = ANY($2::text[])
                 AND c.contype IN ('p', 'f')""",
        schema,
        table_names,
    )
    found_constraints = {
        (
            row["table_name"],
            row["contype"],
            tuple(row["local_columns"]),
            row["referenced_table"],
            row["referenced_schema"],
            tuple(row["referenced_columns"]),
        )
        for row in constraints
    }
    expected_constraints = {
        (EVENT_STORE_META_TABLE, "p", ("marker",), None, None, ()),
        (EVENT_STORE_CHECKPOINTS_TABLE, "p", ("resource",), None, None, ()),
        (EVENT_STORE_PENDING_CALLBACKS_TABLE, "p", ("id",), None, None, ()),
        (
            EVENT_STORE_PENDING_CALLBACKS_TABLE,
            "f",
            ("resource",),
            EVENT_STORE_CHECKPOINTS_TABLE,
            schema,
            ("resource",),
        ),
    }
    if shared:
        expected_constraints.add(
            (EVENT_STORE_COORDINATION_TABLE, "p", ("marker",), None, None, ())
        )
    if found_constraints != expected_constraints:
        raise LifecycleError("PostgreSQL event store constraints do not match")

    index = await connection.fetchrow(
        """SELECT t.relname AS table_name, i.indisunique,
                  ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY
                        AS k(num, pos) JOIN pg_catalog.pg_attribute a
                        ON a.attrelid = t.oid AND a.attnum = k.num
                        ORDER BY k.pos) AS columns
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_index i ON i.indexrelid = c.oid
           JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
           WHERE n.nspname = $1 AND c.relname = $2""",
        schema,
        EVENT_STORE_PENDING_INDEX,
    )
    if (
        index is None
        or index["table_name"] != EVENT_STORE_PENDING_CALLBACKS_TABLE
        or index["indisunique"]
        or tuple(index["columns"]) != ("subscription_id", "id")
    ):
        raise LifecycleError("PostgreSQL event store index does not match")

    sequence = _qualified(schema, EVENT_STORE_PENDING_SEQUENCE)
    pending = _qualified(schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
    owned = await connection.fetchval(
        "SELECT pg_catalog.pg_get_serial_sequence($1, 'id')::regclass = $2::regclass",
        pending,
        sequence,
    )
    if owned is not True:
        raise LifecycleError("PostgreSQL event store callback sequence does not match")

    identity = await connection.fetchval(
        """SELECT a.attidentity::text FROM pg_catalog.pg_attribute a
           WHERE a.attrelid = $1::regclass AND a.attname = 'id'""",
        pending,
    )
    if identity != "a":
        raise LifecycleError("PostgreSQL event store callback IDs must use an identity")


class PostgresEventStore:
    """Keep event checkpoints and pending listener calls in PostgreSQL.

    Open with :meth:`open` or :meth:`from_pool` and close the store when finished.
    The store closes only pools it creates. Use a stable
    subscription ID to replay pending calls after a restart. A listener may
    run twice if its acknowledgement is interrupted after it succeeds.

    The store implements ``CoordinatedEventStore``. Multiple clients must
    explicitly enable shared coordination to elect pollers and consumers.
    """

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._schema = _checked_schema(schema)
        self._owns_pool = False
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def open(cls, dsn: str, *, schema: str = "public") -> Self:
        """Create a connection pool and prepare tables in an existing schema.

        The pool uses one connection. To control pooling or connection
        settings, supply your own pool with :meth:`from_pool`.
        """
        if not dsn:
            raise ConfigurationError("PostgreSQL event storage requires a DSN")
        schema = _checked_schema(schema)
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        store = cls(pool, schema)
        store._owns_pool = True
        try:
            await store._initialise()
        except BaseException:
            await pool.close()
            raise
        return store

    @classmethod
    async def from_pool(cls, pool: asyncpg.Pool, *, schema: str = "public") -> Self:
        """Prepare store objects using a pool owned by the caller.

        Closing the store leaves the supplied pool open. The schema must
        already exist; this method creates only the package's objects.
        """
        store = cls(pool, schema)
        await store._initialise()
        return store

    @property
    def durable(self) -> bool:
        """Whether committed data survives closing and reopening this store."""
        return True

    async def __aenter__(self) -> Self:
        if self._closed:
            raise LifecycleError("PostgreSQL event store is closed")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise LifecycleError("PostgreSQL event store is closed")

    async def _initialise(self) -> None:
        async with self._pool.acquire() as connection:
            await self._prepare_schema(connection)

    async def _prepare_schema(self, conn: PoolConnectionProxy) -> None:
        schema = self._schema
        meta = _qualified(schema, EVENT_STORE_META_TABLE)
        checkpoints = _qualified(schema, EVENT_STORE_CHECKPOINTS_TABLE)
        pending = _qualified(schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
        sequence = _qualified(schema, EVENT_STORE_PENDING_SEQUENCE)
        index = '"' + EVENT_STORE_PENDING_INDEX + '"'
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = $1", schema
            )
            if exists is None:
                raise ConfigurationError("PostgreSQL schema does not exist")
            # Serialise first-time setup without locking unrelated application tables.
            await conn.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtext($1), pg_catalog.hashtext($2))",
                EVENT_STORE_IDENTITY,
                schema,
            )
            existing = await conn.fetchval(
                """SELECT 1 FROM pg_catalog.pg_class c
                   JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = $1 AND c.relname = ANY($2::text[]) LIMIT 1""",
                schema,
                list(_OBJECTS),
            )
            if existing is None:
                await conn.execute(
                    f"CREATE TABLE {meta} (marker TEXT PRIMARY KEY NOT NULL, "
                    "schema_version INTEGER NOT NULL)"
                )
                await conn.execute(
                    f"CREATE TABLE {checkpoints} (resource TEXT PRIMARY KEY NOT NULL, "
                    "version BIGINT NOT NULL CHECK (version >= 1), "
                    "payload BYTEA NOT NULL, revision BIGINT)"
                )
                await conn.execute(
                    f"CREATE TABLE {pending} ("
                    f"id BIGINT GENERATED ALWAYS AS IDENTITY "
                    f"(SEQUENCE NAME {sequence}) PRIMARY KEY, "
                    "resource TEXT NOT NULL, checkpoint_version BIGINT NOT NULL, "
                    "subscription_id TEXT NOT NULL, event TEXT NOT NULL, "
                    "payload BYTEA NOT NULL, "
                    f"FOREIGN KEY (resource) REFERENCES {checkpoints}(resource))"
                )
                await conn.execute(
                    f"CREATE INDEX {index} ON {pending} (subscription_id, id)"
                )
                await conn.execute(
                    f"INSERT INTO {meta} (marker, schema_version) VALUES ($1, $2)",
                    EVENT_STORE_IDENTITY,
                    _SCHEMA_VERSION,
                )
            if existing is not None:
                shared_object = await conn.fetchval(
                    "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 AND c.relname = $2",
                    schema,
                    EVENT_STORE_COORDINATION_TABLE,
                )
                await _validate_schema(conn, schema, shared=shared_object is not None)
            metadata = await conn.fetchval(
                f"SELECT schema_version FROM {meta} WHERE marker = $1",
                EVENT_STORE_IDENTITY,
            )
            coordination = _qualified(schema, EVENT_STORE_COORDINATION_TABLE)
            if metadata == 1:
                await _validate_schema(conn, schema, shared=False)
                await conn.execute(
                    f"CREATE TABLE {coordination} (marker TEXT PRIMARY KEY NOT NULL, payload BYTEA NOT NULL)"
                )
                await conn.execute(
                    f"UPDATE {meta} SET schema_version = $1", _SCHEMA_VERSION
                )
            elif existing is None:
                await conn.execute(
                    f"CREATE TABLE {coordination} (marker TEXT PRIMARY KEY NOT NULL, payload BYTEA NOT NULL)"
                )
            await _validate_schema(conn, schema)

    async def read_checkpoint(
        self, resource: PollingResource
    ) -> StoredCheckpoint | None:
        """Return the most recent checkpoint for a resource, if any."""
        table = _qualified(self._schema, EVENT_STORE_CHECKPOINTS_TABLE)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    f"SELECT version, payload, revision FROM {table} WHERE resource = $1",
                    resource.value,
                )
        if row is None:
            return None
        return StoredCheckpoint(
            resource, row["version"], row["payload"], row["revision"]
        )

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
        """Save a checkpoint and pending calls atomically if its version matches."""
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
        checkpoints = _qualified(self._schema, EVENT_STORE_CHECKPOINTS_TABLE)
        pending = _qualified(self._schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    if callable(callbacks):
                        await self._lock_shared(conn)
                        state, now = await self._shared_state(conn)
                        intents = tuple(
                            checked_intent(intent) for intent in callbacks(state, now)
                        )
                    # Serialize capacity checks across resources, without claiming a delivery lease.
                    await conn.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext($1), pg_catalog.hashtext($2))",
                        EVENT_STORE_IDENTITY,
                        self._schema + ":callbacks",
                    )
                    # The conditional write also protects against other processes.
                    if expected_version is None:
                        row = await conn.fetchrow(
                            f"""INSERT INTO {checkpoints}
                               (resource, version, payload, revision)
                               VALUES ($1, 1, $2, $3) ON CONFLICT (resource) DO NOTHING
                               RETURNING version""",
                            resource.value,
                            payload,
                            revision,
                        )
                    else:
                        row = await conn.fetchrow(
                            f"""UPDATE {checkpoints}
                               SET version = version + 1, payload = $2, revision = $3
                               WHERE resource = $1 AND version = $4 RETURNING version""",
                            resource.value,
                            payload,
                            revision,
                            expected_version,
                        )
                    if row is None:
                        return None
                    if intents and max_pending_callbacks is not None:
                        count = cast(
                            int, await conn.fetchval(f"SELECT COUNT(*) FROM {pending}")
                        )
                        if count + len(intents) > max_pending_callbacks:
                            raise PendingCallbackLimit(
                                "event store pending callback limit reached"
                            )
                    next_version = cast(int, row["version"])
                    records: list[StoredCallback] = []
                    for intent in intents:
                        callback_id = await conn.fetchval(
                            f"""INSERT INTO {pending}
                               (resource, checkpoint_version, subscription_id,
                                event, payload) VALUES ($1, $2, $3, $4, $5)
                               RETURNING id""",
                            resource.value,
                            next_version,
                            intent.subscription_id,
                            intent.event,
                            intent.payload,
                        )
                        records.append(
                            StoredCallback(
                                cast(int, callback_id),
                                resource,
                                next_version,
                                intent.subscription_id,
                                intent.event,
                                intent.payload,
                            )
                        )
        checkpoint = StoredCheckpoint(resource, next_version, payload, revision)
        return CommitResult(checkpoint, tuple(records))

    async def pending_callbacks(
        self, subscription_id: str, *, limit: int
    ) -> tuple[StoredCallback, ...]:
        """Read unacknowledged calls for a subscription in callback ID order."""
        checked_subscription_id(subscription_id)
        checked_positive_int(limit, "limit")
        table = _qualified(self._schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as connection:
                rows = await connection.fetch(
                    f"""SELECT id, resource, checkpoint_version, event, payload
                       FROM {table} WHERE subscription_id = $1 ORDER BY id LIMIT $2""",
                    subscription_id,
                    limit,
                )
        return tuple(
            StoredCallback(
                row["id"],
                PollingResource(row["resource"]),
                row["checkpoint_version"],
                subscription_id,
                row["event"],
                row["payload"],
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
        table = _qualified(self._schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    if check is not None:
                        await self._lock_shared(conn)
                        state, now = await self._shared_state(conn)
                        check(state, now)
                    result = await conn.fetchval(
                        f"DELETE FROM {table} WHERE subscription_id = $1 AND id = $2 RETURNING id",
                        subscription_id,
                        callback_id,
                    )
        return result is not None

    async def _lock_shared(self, conn: PoolConnectionProxy) -> None:
        await conn.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext($1), pg_catalog.hashtext($2))",
            EVENT_STORE_IDENTITY,
            self._schema + ":coordination",
        )

    async def _shared_state(
        self, conn: PoolConnectionProxy
    ) -> tuple[bytes | None, float]:
        table = _qualified(self._schema, EVENT_STORE_COORDINATION_TABLE)
        rows = await conn.fetch(f"SELECT marker, payload FROM {table} LIMIT 2")
        if len(rows) > 1 or (rows and rows[0]["marker"] != EVENT_STORE_IDENTITY):
            raise LifecycleError("PostgreSQL coordination state marker does not match")
        now = cast(
            float,
            await conn.fetchval(
                "SELECT extract(epoch FROM clock_timestamp())::double precision"
            ),
        )
        return (cast(bytes, rows[0]["payload"]) if rows else None, now)

    async def coordinate(self, update: SharedUpdate[_Result]) -> _Result:
        """Update shared state atomically using the database's UTC clock."""
        table = _qualified(self._schema, EVENT_STORE_COORDINATION_TABLE)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await self._lock_shared(conn)
                    previous, now = await self._shared_state(conn)
                    payload, result = update(previous, now)
                    checked_bytes(payload, "coordination payload")
                    await conn.execute(
                        f"INSERT INTO {table} (marker, payload) VALUES ($1, $2) ON CONFLICT(marker) DO UPDATE SET payload = excluded.payload",
                        EVENT_STORE_IDENTITY,
                        payload,
                    )
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
        """Close an owned pool without discarding pending calls."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_pool:
                await self._pool.close()

    async def count_pending_callbacks(self, subscription_id: str | None = None) -> int:
        """Count saved calls, optionally selecting one subscription."""
        if subscription_id is not None:
            checked_subscription_id(subscription_id)
        table = _qualified(self._schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)
        sql = f"SELECT COUNT(*) FROM {table}"
        args: tuple[object, ...] = ()
        if subscription_id is not None:
            sql += " WHERE subscription_id = $1"
            args = (subscription_id,)
        async with self._lock:
            self._require_open()
            async with self._pool.acquire() as connection:
                return cast(int, await connection.fetchval(sql, *args))
