import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def pg_database() -> AsyncIterator[tuple[str, str]]:
    dsn = os.environ.get("DANKMEMER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "set DANKMEMER_TEST_POSTGRES_DSN to run PostgreSQL integration tests"
        )
    # Quotes in the test schema exercise identifier escaping in every store query.
    schema = "test_dankmemer_'\"_" + uuid4().hex
    quoted = '"' + schema.replace('"', '""') + '"'
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f"CREATE SCHEMA {quoted}")
        try:
            yield dsn, schema
        finally:
            await connection.execute(f"DROP SCHEMA {quoted} CASCADE")
    finally:
        await connection.close()
