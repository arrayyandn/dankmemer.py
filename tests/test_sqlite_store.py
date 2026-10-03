import asyncio
import sqlite3
from pathlib import Path

import pytest

from dankmemer._coordination_storage import EVENT_STORE_COORDINATION_TABLE
from dankmemer._sqlite_storage import SqliteEventStore
from dankmemer._storage import CallbackIntent
from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError, LifecycleError


@pytest.mark.asyncio
async def test_sqlite_store_rejects_in_memory_database() -> None:
    with pytest.raises(ConfigurationError, match="requires a file path"):
        await SqliteEventStore.open(":memory:")


@pytest.mark.asyncio
async def test_sqlite_names_have_permanent_marker_and_separate_schema_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shared.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE dankmemer_py_events_checkpoints (service_value TEXT)"
        )

    async with await SqliteEventStore.open(path):
        pass

    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name LIKE 'dankmemer_py_events_%' "
                "AND name NOT LIKE 'sqlite_autoindex_%'"
            )
        }
        assert names == {
            "dankmemer_py_events_checkpoints",
            "dankmemer_py_events_meta_010926",
            "dankmemer_py_events_coordination_010926",
            "dankmemer_py_events_checkpoints_010926",
            "dankmemer_py_events_pending_callbacks_010926",
            "dankmemer_py_events_pending_by_subscription_010926",
        }
        assert connection.execute(
            "SELECT marker, schema_version FROM dankmemer_py_events_meta_010926"
        ).fetchone() == ("dankmemer.py/events/010926", 2)


@pytest.mark.asyncio
async def test_sqlite_refuses_existing_table_with_its_name(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE dankmemer_py_events_checkpoints_010926 (service_value TEXT)"
        )
        connection.execute(
            "INSERT INTO dankmemer_py_events_checkpoints_010926 VALUES ('owned')"
        )

    with pytest.raises(LifecycleError, match="names conflict"):
        await SqliteEventStore.open(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT service_value FROM dankmemer_py_events_checkpoints_010926"
        ).fetchall() == [("owned",)]
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = "
                "'dankmemer_py_events_meta_010926'"
            ).fetchone()
            is None
        )


@pytest.mark.asyncio
async def test_sqlite_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    async with await SqliteEventStore.open(path):
        pass

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE dankmemer_py_events_meta_010926 SET schema_version = 999"
        )

    with pytest.raises(LifecycleError, match="schema version"):
        await SqliteEventStore.open(path)


@pytest.mark.asyncio
async def test_sqlite_migrates_v1_without_losing_pending_callbacks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite"
    async with await SqliteEventStore.open(path) as store:
        first = await store.commit(
            PollingResource.BLOGS,
            expected_version=None,
            payload=b"baseline",
            revision=5,
            callbacks=(CallbackIntent("saved", "blog_published", b"arguments"),),
        )
        assert first is not None
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP TABLE {EVENT_STORE_COORDINATION_TABLE}")
        connection.execute(
            "UPDATE dankmemer_py_events_meta_010926 SET schema_version = 1"
        )
    async with await SqliteEventStore.open(path) as store:
        assert await store.read_checkpoint(PollingResource.BLOGS) == first.checkpoint
        assert await store.pending_callbacks("saved", limit=1) == first.callbacks
        assert await store.acknowledge("saved", first.callbacks[0].id)
        result = await store.commit(
            PollingResource.BLOGS,
            expected_version=1,
            payload=b"next",
            revision=6,
            callbacks=(),
        )
        assert result is not None and result.checkpoint.version == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM dankmemer_py_events_meta_010926"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_cancelled_sqlite_writer_releases_transaction_after_lock_wait(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cancellation.sqlite"
    async with await SqliteEventStore.open(path) as store:
        blocker = sqlite3.connect(path, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(
            store.coordinate(lambda state, now: (b"saved", None))
        )
        try:
            await asyncio.sleep(0.025)
            task.cancel()
            await asyncio.sleep(0.025)
            task.cancel()
            blocker.rollback()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await store.coordinate(lambda state, now: (b"next", state)) is None
            assert (
                await store.coordinate(lambda state, now: (b"next", state)) == b"next"
            )
        finally:
            blocker.rollback()
            blocker.close()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sqlite_rejects_modified_store_table(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    async with await SqliteEventStore.open(path):
        pass

    with sqlite3.connect(path) as connection:
        connection.execute(
            "ALTER TABLE dankmemer_py_events_checkpoints_010926 "
            "ADD COLUMN foreign_value TEXT"
        )

    with pytest.raises(LifecycleError, match="unexpected schema"):
        await SqliteEventStore.open(path)


@pytest.mark.asyncio
async def test_sqlite_failed_callback_insert_rolls_back_whole_commit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.db"
    async with await SqliteEventStore.open(path) as store:
        baseline = await store.commit(
            PollingResource.DROPS,
            expected_version=None,
            payload=b"baseline",
            revision=1,
            callbacks=[],
        )
        assert baseline is not None

        with sqlite3.connect(path) as connection:
            connection.execute(
                """CREATE TRIGGER reject_bad_event
                   BEFORE INSERT ON dankmemer_py_events_pending_callbacks_010926
                   WHEN NEW.event = 'bad'
                   BEGIN SELECT RAISE(ABORT, 'bad callback'); END"""
            )

        with pytest.raises(sqlite3.IntegrityError, match="bad callback"):
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

    async with await SqliteEventStore.open(path) as reopened:
        assert (
            await reopened.read_checkpoint(PollingResource.DROPS) == baseline.checkpoint
        )
        assert await reopened.pending_callbacks("bot.drops", limit=10) == ()
