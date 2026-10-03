import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from event_helpers import durable, offline_client, wait_count
from resource_helpers import Responses, blogs, item, local_server
from store_helpers import database

from dankmemer import (
    Blog,
    ClientState,
    CoordinationConfig,
    CoordinationMode,
    DankMemer,
    EventConfig,
    LifecycleError,
)
from dankmemer.storage.postgres import PostgresEventStore
from dankmemer.storage.sqlite import SqliteEventStore


async def wait_closing(client: DankMemer) -> None:
    async with asyncio.timeout(2):
        while client.state is not ClientState.CLOSING:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_grace_finishes_callback_and_keeps_queued_durable_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    observed: list[str] = []
    async with await SqliteEventStore.open(tmp_path / "events.sqlite") as store:
        client = offline_client(
            monkeypatch,
            Responses(blogs(2, 1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        async def callback(blog: Blog) -> None:
            observed.append(blog.id)
            entered.set()
            await release.wait()

        client.add_listener(callback, name="blog_published", subscription_id="alerts")
        try:
            await client.start()
            await asyncio.wait_for(entered.wait(), timeout=2)
            closing = asyncio.create_task(client.close(grace_seconds=2))
            await wait_closing(client)
            await asyncio.sleep(0)
            assert not closing.done()
            with pytest.raises(LifecycleError):
                await client.blogs.latest()
            release.set()
            await asyncio.wait_for(closing, timeout=1)
            assert observed == ["1"]
            assert await store.count_pending_callbacks("alerts") == 1
        finally:
            release.set()
            await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("grace_seconds", [None, 0.0, 0.02])
async def test_expired_grace_cancels_callback_and_preserves_durable_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, grace_seconds: float | None
) -> None:
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    async with await SqliteEventStore.open(tmp_path / "events.sqlite") as store:
        client = offline_client(
            monkeypatch,
            Responses(blogs(2, 1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        async def callback(blog: Blog) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                interrupted.set()

        client.add_listener(callback, name="blog_published", subscription_id="alerts")
        try:
            await client.start()
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(client.close(grace_seconds=grace_seconds), timeout=1)
            assert interrupted.is_set() and client.is_closed
            assert await store.count_pending_callbacks("alerts") == 2
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_grace_finishes_best_effort_callback_without_starting_queued_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    observed: list[str] = []
    client = offline_client(
        monkeypatch,
        Responses(blogs(2, 1)),
        events=EventConfig(emit_initial=True),
    )

    async def callback(blog: Blog) -> None:
        observed.append(blog.id)
        entered.set()
        await release.wait()

    client.add_listener(callback, name="blog_published")
    try:
        await client.start()
        await asyncio.wait_for(entered.wait(), timeout=2)
        closing = asyncio.create_task(client.close(grace_seconds=2))
        await wait_closing(client)
        release.set()
        await asyncio.wait_for(closing, timeout=1)
        assert observed == ["1"]
        assert await client._events.count_pending(None) == 0  # pyright: ignore[reportPrivateUsage]
    finally:
        release.set()
        await client.close()


@pytest.mark.asyncio
async def test_grace_waits_for_request_completion_without_waiting_for_its_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    received = asyncio.Event()
    caller_exit = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        entered.set()
        await release.wait()
        return web.json_response(False)

    async with local_server(handler) as url:
        monkeypatch.setattr("dankmemer.http._transport._API_BASE_URL", url)
        async with aiohttp.ClientSession() as session:
            client = DankMemer("test-token", session=session)

            async def application_task() -> None:
                assert await client.users.is_banned(123) is False
                received.set()
                await caller_exit.wait()

            caller = asyncio.create_task(application_task())
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                closing = asyncio.create_task(client.close(grace_seconds=2))
                await wait_closing(client)
                release.set()
                await asyncio.wait_for(closing, timeout=1)
                assert received.is_set() and not caller.done()
                assert not session.closed
            finally:
                release.set()
                caller_exit.set()
                await asyncio.gather(caller, return_exceptions=True)
                await client.close()


@pytest.mark.asyncio
async def test_grace_allows_a_cached_catalog_load_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        entered.set()
        await release.wait()
        return web.json_response(
            {"data": [item(158, "Life Saver")], "nextCursor": None}
        )

    async with local_server(handler) as url:
        monkeypatch.setattr("dankmemer.http._transport._API_BASE_URL", url)
        client = DankMemer("test-token")
        lookup = asyncio.create_task(client.items.get("Life Saver"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            closing = asyncio.create_task(client.close(grace_seconds=2))
            await wait_closing(client)
            release.set()
            await asyncio.wait_for(closing, timeout=1)
            result = await lookup
            assert result is not None and result.name == "Life Saver"
        finally:
            release.set()
            await client.close()
            await asyncio.gather(lookup, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_grace_cancels_an_unfinished_catalog_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        entered.set()
        await release.wait()
        return web.json_response({"data": [], "nextCursor": None})

    async with local_server(handler) as url:
        monkeypatch.setattr("dankmemer.http._transport._API_BASE_URL", url)
        client = DankMemer("test-token")
        lookup = asyncio.create_task(client.items.get("Life Saver"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(client.close(grace_seconds=0.02), timeout=1)
            with pytest.raises(asyncio.CancelledError):
                await lookup
            assert client.is_closed
        finally:
            release.set()
            await client.close()
            await asyncio.gather(lookup, return_exceptions=True)


@pytest.mark.asyncio
async def test_first_close_keeps_its_grace_when_another_caller_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    async with await SqliteEventStore.open(tmp_path / "events.sqlite") as store:
        client = offline_client(
            monkeypatch,
            Responses(blogs(1)),
            events=durable(emit_initial=True),
            event_store=store,
        )

        async def callback(blog: Blog) -> None:
            entered.set()
            await release.wait()

        client.add_listener(callback, name="blog_published", subscription_id="alerts")
        try:
            await client.start()
            await asyncio.wait_for(entered.wait(), timeout=2)
            first = asyncio.create_task(client.close(grace_seconds=2))
            await wait_closing(client)
            other = asyncio.create_task(client.close())
            await asyncio.sleep(0)
            other.cancel()
            with pytest.raises(asyncio.CancelledError):
                await other
            assert not first.done()
            release.set()
            await asyncio.wait_for(first, timeout=1)
            await wait_count(store, 0)
        finally:
            release.set()
            await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_shared_ownership_is_renewed_until_grace_callback_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    repeated = asyncio.Event()
    monkeypatch.setattr(DankMemer, "_request_json", Responses(blogs(1)))
    coordination = CoordinationConfig(
        mode=CoordinationMode.SHARED,
        application_id="shutdown-tests",
        lease_seconds=1.2,
        heartbeat_seconds=0.2,
        check_seconds=0.1,
    )
    async with database(backend, tmp_path) as (left_store, location, schema):
        right_store = (
            await SqliteEventStore.open(location)
            if backend == "sqlite"
            else await PostgresEventStore.open(location, schema=schema)
        )
        async with right_store:
            left = DankMemer(
                "test-token",
                events=durable(emit_initial=True),
                event_store=left_store,
                coordination=coordination,
            )
            right = DankMemer(
                "test-token",
                events=durable(emit_initial=True),
                event_store=right_store,
                coordination=coordination,
            )

            async def first(blog: Blog) -> None:
                entered.set()
                await release.wait()

            async def second(blog: Blog) -> None:
                repeated.set()

            left.add_listener(first, name="blog_published", subscription_id="alerts")
            right.add_listener(second, name="blog_published", subscription_id="alerts")
            try:
                await left.start()
                await asyncio.wait_for(entered.wait(), timeout=3)
                await right.start()
                closing = asyncio.create_task(left.close(grace_seconds=5))
                await wait_closing(left)
                # Wait longer than a lease to catch premature ownership release.
                await asyncio.sleep(1.5)
                assert not closing.done() and not repeated.is_set()
                release.set()
                await asyncio.wait_for(closing, timeout=2)
                assert await left_store.count_pending_callbacks("alerts") == 0
                await asyncio.sleep(0.2)
                assert not repeated.is_set()
            finally:
                release.set()
                await left.close()
                await right.close()
