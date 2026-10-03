import asyncio

import asyncpg
import pytest
from event_helpers import durable, lottery, offline_client, wait_count, wait_paused
from resource_helpers import Responses

from dankmemer import DisabledPolling, LotteryResult, PollingConfig
from dankmemer._coordination_storage import EVENT_STORE_COORDINATION_TABLE
from dankmemer._postgres_storage import PostgresEventStore
from dankmemer._storage import (
    EVENT_STORE_CHECKPOINTS_TABLE,
    EVENT_STORE_IDENTITY,
    EVENT_STORE_META_TABLE,
    EVENT_STORE_PENDING_CALLBACKS_TABLE,
    EVENT_STORE_PENDING_INDEX,
    EVENT_STORE_PENDING_SEQUENCE,
    CallbackIntent,
)
from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError, LifecycleError


def _table(schema: str, name: str) -> str:
    return '"' + schema.replace('"', '""') + '"."' + name + '"'


@pytest.mark.asyncio
async def test_postgres_public_client_replays_after_reopening_store(
    pg_database: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn, schema = pg_database
    async with await PostgresEventStore.open(dsn, schema=schema) as store:
        first = offline_client(
            monkeypatch,
            Responses(lottery(1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        @first.event
        async def on_lottery_result(result: LotteryResult) -> None:
            raise RuntimeError("temporary callback failure")

        async with first:
            await wait_paused(first, "on_lottery_result")
            assert await first.count_pending_events() == 1

    requests = Responses()
    replayed = asyncio.Event()
    async with await PostgresEventStore.open(dsn, schema=schema) as store:
        second = offline_client(
            monkeypatch,
            requests,
            events=durable(),
            event_store=store,
            polling=PollingConfig(lottery=DisabledPolling()),
        )

        @second.event
        async def on_lottery_result(result: LotteryResult) -> None:
            assert result.winnings == 100
            replayed.set()

        async with second:
            await asyncio.wait_for(replayed.wait(), timeout=3)
            await wait_count(store, 0)
            assert not requests.calls


@pytest.mark.asyncio
async def test_postgres_requires_dsn_and_schema_name() -> None:
    with pytest.raises(ConfigurationError, match="DSN"):
        await PostgresEventStore.open("")
    with pytest.raises(ConfigurationError, match="schema name"):
        await PostgresEventStore.open("postgresql://unused", schema="")


@pytest.mark.asyncio
async def test_postgres_preserves_other_tables_and_uses_permanent_names(
    pg_database: tuple[str, str],
) -> None:
    dsn, schema = pg_database
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            f"CREATE TABLE {_table(schema, 'dankmemer_py_events_checkpoints')} "
            "(service_value TEXT)"
        )
        async with await PostgresEventStore.open(dsn, schema=schema) as store:
            assert store.durable
        rows = await connection.fetch(
            """SELECT c.relname FROM pg_catalog.pg_class c
               JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = $1 AND c.relkind IN ('r', 'S')""",
            schema,
        )
        assert {row["relname"] for row in rows} == {
            "dankmemer_py_events_checkpoints",
            EVENT_STORE_META_TABLE,
            EVENT_STORE_CHECKPOINTS_TABLE,
            EVENT_STORE_PENDING_CALLBACKS_TABLE,
            EVENT_STORE_PENDING_SEQUENCE,
            EVENT_STORE_COORDINATION_TABLE,
        }
        metadata = await connection.fetchrow(
            f"SELECT marker, schema_version FROM {_table(schema, EVENT_STORE_META_TABLE)}"
        )
        assert metadata is not None
        assert tuple(metadata.values()) == (EVENT_STORE_IDENTITY, 2)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_postgres_failed_callback_insert_rolls_back_whole_commit(
    pg_database: tuple[str, str],
) -> None:
    dsn, schema = pg_database
    async with await PostgresEventStore.open(dsn, schema=schema) as store:
        baseline = await store.commit(
            PollingResource.DROPS,
            expected_version=None,
            payload=b"baseline",
            revision=1,
            callbacks=[],
        )
        assert baseline is not None
        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute(
                f"ALTER TABLE {_table(schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)} "
                "ADD CONSTRAINT reject_bad_event CHECK (event <> 'bad')"
            )
        finally:
            await connection.close()
        with pytest.raises(asyncpg.CheckViolationError):
            await store.commit(
                PollingResource.DROPS,
                expected_version=1,
                payload=b"candidate",
                revision=2,
                callbacks=[
                    CallbackIntent("bot.drops", "drop_started", b"good"),
                    CallbackIntent("bot.drops", "bad", b"bad"),
                ],
            )
        assert await store.read_checkpoint(PollingResource.DROPS) == baseline.checkpoint
        assert await store.pending_callbacks("bot.drops", limit=10) == ()
    async with await PostgresEventStore.open(dsn, schema=schema) as reopened:
        assert (
            await reopened.read_checkpoint(PollingResource.DROPS) == baseline.checkpoint
        )
        assert await reopened.pending_callbacks("bot.drops", limit=10) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "marker", "columns", "index"])
async def test_postgres_rejects_changed_store_schema(
    pg_database: tuple[str, str],
    change: str,
) -> None:
    dsn, schema = pg_database
    async with await PostgresEventStore.open(dsn, schema=schema):
        pass
    connection = await asyncpg.connect(dsn)
    try:
        if change == "version":
            await connection.execute(
                f"UPDATE {_table(schema, EVENT_STORE_META_TABLE)} SET schema_version = 999"
            )
        elif change == "marker":
            await connection.execute(
                f"UPDATE {_table(schema, EVENT_STORE_META_TABLE)} SET marker = 'foreign'"
            )
        elif change == "columns":
            await connection.execute(
                f"ALTER TABLE {_table(schema, EVENT_STORE_CHECKPOINTS_TABLE)} "
                "ADD COLUMN foreign_value TEXT"
            )
        else:
            await connection.execute(
                f"DROP INDEX {_table(schema, EVENT_STORE_PENDING_INDEX)}"
            )
            await connection.execute(
                f'CREATE INDEX "{EVENT_STORE_PENDING_INDEX}" ON '
                f"{_table(schema, EVENT_STORE_PENDING_CALLBACKS_TABLE)} (event, id)"
            )
        with pytest.raises(LifecycleError):
            await PostgresEventStore.open(dsn, schema=schema)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_postgres_collision_does_not_change_other_objects_or_close_borrowed_pool(
    pg_database: tuple[str, str],
) -> None:
    dsn, schema = pg_database
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        await pool.execute(
            f"CREATE TABLE {_table(schema, EVENT_STORE_CHECKPOINTS_TABLE)} "
            "(service_value TEXT)"
        )
        await pool.execute(
            f"INSERT INTO {_table(schema, EVENT_STORE_CHECKPOINTS_TABLE)} VALUES ('owned')"
        )
        with pytest.raises(LifecycleError, match="names conflict"):
            await PostgresEventStore.from_pool(pool, schema=schema)
        assert (
            await pool.fetchval(
                f"SELECT service_value FROM {_table(schema, EVENT_STORE_CHECKPOINTS_TABLE)}"
            )
            == "owned"
        )
        assert (
            await pool.fetchval(
                "SELECT to_regclass($1)", _table(schema, EVENT_STORE_META_TABLE)
            )
            is None
        )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_postgres_close_keeps_callers_pool_open(
    pg_database: tuple[str, str],
) -> None:
    dsn, schema = pg_database
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        store = await PostgresEventStore.from_pool(pool, schema=schema)
        await store.close()
        await store.close()
        assert await pool.fetchval("SELECT 1") == 1
        with pytest.raises(LifecycleError, match="closed"):
            await store.read_checkpoint(PollingResource.DROPS)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_postgres_migrates_v1_without_losing_pending_callbacks(
    pg_database: tuple[str, str],
) -> None:
    dsn, schema = pg_database
    async with await PostgresEventStore.open(dsn, schema=schema) as store:
        saved = await store.commit(
            PollingResource.BLOGS,
            expected_version=None,
            payload=b"baseline",
            revision=5,
            callbacks=(CallbackIntent("saved", "blog_published", b"args"),),
        )
        assert saved is not None
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            f"DROP TABLE {_table(schema, EVENT_STORE_COORDINATION_TABLE)}"
        )
        await connection.execute(
            f"UPDATE {_table(schema, EVENT_STORE_META_TABLE)} SET schema_version = 1"
        )
        async with await PostgresEventStore.open(dsn, schema=schema) as store:
            assert (
                await store.read_checkpoint(PollingResource.BLOGS) == saved.checkpoint
            )
            assert await store.pending_callbacks("saved", limit=1) == saved.callbacks
            assert await store.acknowledge("saved", saved.callbacks[0].id)
        assert (
            await connection.fetchval(
                f"SELECT schema_version FROM {_table(schema, EVENT_STORE_META_TABLE)}"
            )
            == 2
        )
    finally:
        await connection.close()
