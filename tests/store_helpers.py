import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from dankmemer import CoordinatedEventStore
from dankmemer.storage.postgres import PostgresEventStore
from dankmemer.storage.sqlite import SqliteEventStore


@asynccontextmanager
async def open_store(
    backend: str, location: str, schema: str
) -> AsyncGenerator[CoordinatedEventStore, None]:
    if backend == "sqlite":
        async with await SqliteEventStore.open(location) as store:
            yield store
    else:
        async with await PostgresEventStore.open(location, schema=schema) as pg_store:
            yield pg_store


@asynccontextmanager
async def database(
    backend: str, directory: Path
) -> AsyncGenerator[tuple[CoordinatedEventStore, str, str], None]:
    if backend == "sqlite":
        location = str(directory / "process.sqlite")
        async with await SqliteEventStore.open(location) as store:
            yield store, location, ""
        return
    dsn = os.environ.get("DANKMEMER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set DANKMEMER_TEST_POSTGRES_DSN for PostgreSQL integration")
    schema = "process_" + uuid4().hex
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        async with await PostgresEventStore.open(dsn, schema=schema) as pg_store:
            yield pg_store, dsn, schema
    finally:
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()
