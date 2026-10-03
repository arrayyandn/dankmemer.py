# pyright: reportPrivateUsage=false
import asyncio
import gzip
from collections.abc import Callable
from typing import cast

import aiohttp
import pytest
from aiohttp import web
from resource_helpers import ManualClock as AutoAdvanceClock
from resource_helpers import local_server

from dankmemer.client import DankMemer
from dankmemer.config import ApplicationIdentity, RequestConfig, RetryConfig
from dankmemer.enums import PollingResource
from dankmemer.errors import (
    ConfigurationError,
    DankMemerHTTPError,
    DankMemerResponseError,
    DankMemerTimeoutError,
    LifecycleError,
    RateLimited,
)
from dankmemer.http._admission import Admission
from dankmemer.http._routes import (
    BLOGS,
    DROPS,
    FISH,
    ITEMS,
    STORE_DAILY_GIFTS,
    STREAM_TRENDING_GAME,
    USER_BAN_STATUS,
    Route,
    check_route,
)
from dankmemer.http._transport import Transport, _retry_after


class FakeClock(AutoAdvanceClock):
    def __init__(self) -> None:
        super().__init__()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        await super().sleep(seconds)


class BlockingClock(FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self.waiting = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.waiting.set()
        await asyncio.Event().wait()


def transport(
    session: aiohttp.ClientSession,
    clock: FakeClock,
    url: str,
    *,
    request: RequestConfig | None = None,
    retry: RetryConfig | None = None,
    origin: str | None = None,
    application: ApplicationIdentity | None = None,
) -> Transport:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("dankmemer.http._transport._API_BASE_URL", url)
        return Transport(
            session,
            "secret-token",
            origin=origin,
            application=application,
            request=request or RequestConfig(),
            retry=retry or RetryConfig(jitter_ratio=0),
            admission=Admission(clock),
        )


def _assert_invalid_target(route: Route, **kwargs: object) -> None:
    with pytest.raises(ConfigurationError):
        factory = cast(Callable[..., object], route.target)
        factory(**kwargs)


def test_route_registry_validates_paths_and_queries() -> None:
    assert USER_BAN_STATUS.target(user_id=123)[0] == "/users/123/ban-status"
    assert BLOGS.target(cursor=0, limit=100)[1] == {"cursor": 0, "limit": 100}
    assert FISH.target(category="tools", limit=2)[1] == {
        "category": "tools",
        "limit": 2,
    }
    for route, resource in (
        (STORE_DAILY_GIFTS, PollingResource.STORE_DAILY_GIFTS),
        (STREAM_TRENDING_GAME, PollingResource.STREAM_TRENDING_GAME),
    ):
        check_route(route)
        assert route.event_resource is resource
        assert route.target()[1] == {}
        _assert_invalid_target(route, limit=1)
    for route, kwargs in (
        (USER_BAN_STATUS, {"user_id": True}),
        (USER_BAN_STATUS, {"user_id": "123"}),
        (BLOGS, {"cursor": -1}),
        (BLOGS, {"limit": 101}),
        (ITEMS, {"category": "tools"}),
        (FISH, {"category": "unknown"}),
        (DROPS, {"cursor": 0}),
    ):
        _assert_invalid_target(route, **kwargs)


@pytest.mark.asyncio
async def test_request_identity_and_query_are_request_local() -> None:
    seen: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        seen.append((request.path, dict(request.query), dict(request.headers)))
        return web.json_response({"data": [], "nextCursor": None})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(
            session,
            FakeClock(),
            url,
            origin="https://example.test",
            application=ApplicationIdentity(name="my-bot", version="2"),
        )
        result = await client.get(ITEMS, cursor=3, limit=2)
        assert result == {"data": [], "nextCursor": None}
        assert "Authorization" not in session.headers
        assert "Origin" not in session.headers

    path, query, headers = seen[0]
    assert path == "/api/v1/items"
    assert query == {"cursor": "3", "limit": "2"}
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert headers["Origin"] == "https://example.test"
    assert headers["User-Agent"].startswith("my-bot/2 dankmemer.py/")


@pytest.mark.asyncio
async def test_rate_limit_retry_after_is_not_capped_and_spaces_attempts() -> None:
    clock = FakeClock()
    attempt_times: list[float] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        attempt_times.append(clock.monotonic())
        if len(attempt_times) == 1:
            return web.Response(
                status=429, text="secret-token", headers={"Retry-After": "120"}
            )
        return web.json_response({"ok": True})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(
            session, clock, url, request=RequestConfig(total_timeout_seconds=180)
        )
        assert await client.get(DROPS, automatic=True) == {"ok": True}

    assert attempt_times == [0, 120]
    assert clock.sleeps == [120]


@pytest.mark.asyncio
async def test_retry_after_beyond_operation_budget_preserves_cooldown() -> None:
    clock = FakeClock()
    count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal count
        count += 1
        return web.Response(status=429, headers={"Retry-After": "120"})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(
            session, clock, url, request=RequestConfig(total_timeout_seconds=30)
        )
        with pytest.raises(RateLimited) as raised:
            await client.get(DROPS, automatic=True)
        assert raised.value.retry_after_seconds == 120
        with pytest.raises(DankMemerTimeoutError):
            await client.get(ITEMS)

    assert count == 1


@pytest.mark.asyncio
async def test_503_waits_at_least_five_seconds_even_with_lower_backoff_cap() -> None:
    clock = FakeClock()
    attempt_times: list[float] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        attempt_times.append(clock.monotonic())
        if len(attempt_times) == 1:
            return web.Response(status=503, text="temporarily unavailable")
        return web.json_response({"ok": True})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(
            session,
            clock,
            url,
            retry=RetryConfig(
                initial_backoff_seconds=1,
                max_backoff_seconds=2,
                jitter_ratio=0,
            ),
        )
        assert await client.get(ITEMS) == {"ok": True}

    assert attempt_times == [0, 5]


@pytest.mark.asyncio
async def test_503_retry_after_extends_the_server_backoff() -> None:
    clock = FakeClock()
    attempt_times: list[float] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        attempt_times.append(clock.monotonic())
        if len(attempt_times) == 1:
            return web.Response(status=503, headers={"Retry-After": "10"})
        return web.json_response({"ok": True})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(session, clock, url)
        assert await client.get(ITEMS) == {"ok": True}

    assert attempt_times == [0, 10]


@pytest.mark.asyncio
async def test_redirect_error_and_ban_id_are_not_leaked() -> None:
    count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal count
        count += 1
        return web.Response(status=302, headers={"Location": "https://other.example/"})

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(session, FakeClock(), url)
        with pytest.raises(DankMemerHTTPError) as raised:
            await client.get(USER_BAN_STATUS, user_id=123456789012345678)

    assert count == 1
    assert raised.value.status_code == 302
    assert raised.value.path == "/users/{user_id}/ban-status"
    assert "123456789012345678" not in str(raised.value)
    assert "secret-token" not in str(raised.value)


@pytest.mark.asyncio
async def test_success_body_limit_invalid_json_and_no_content() -> None:
    responses = [
        web.Response(body=b"x" * 64, content_type="application/json"),
        web.Response(
            body=gzip.compress(b"x" * 64),
            content_type="application/json",
            headers={"Content-Encoding": "gzip"},
        ),
        web.Response(text="{broken", content_type="application/json"),
        web.Response(status=204),
    ]

    async def handler(request: web.Request) -> web.StreamResponse:
        return responses.pop(0)

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(
            session,
            FakeClock(),
            url,
            request=RequestConfig(max_response_bytes=32),
        )
        with pytest.raises(DankMemerResponseError, match="byte limit"):
            await client.get(ITEMS)
        with pytest.raises(DankMemerResponseError, match="byte limit"):
            await client.get(ITEMS)
        with pytest.raises(DankMemerResponseError, match="valid JSON") as raised:
            await client.get(ITEMS)
        assert raised.value.__context__ is None
        assert await client.get(ITEMS) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_nontransient_client_errors_are_not_retried(status: int) -> None:
    count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal count
        count += 1
        return web.Response(status=status, text="secret-token")

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = transport(session, FakeClock(), url)
        with pytest.raises(DankMemerHTTPError) as raised:
            await client.get(ITEMS)

    assert count == 1
    assert raised.value.status_code == status
    assert "secret-token" not in str(raised.value)


@pytest.mark.asyncio
async def test_borrowed_session_conflicts_are_rejected() -> None:
    async with aiohttp.ClientSession(
        headers={"Authorization": "Basic other"}
    ) as session:
        with pytest.raises(ConfigurationError, match="headers"):
            Transport(
                session,
                "secret-token",
                origin=None,
                application=None,
                request=RequestConfig(),
                retry=RetryConfig(),
                admission=Admission(FakeClock()),
            )


@pytest.mark.asyncio
async def test_client_gate_spaces_automatic_resources_and_lower_minute_limit() -> None:
    clock = FakeClock()
    gate = Admission(clock)
    await gate.acquire(
        path="/drops", resource=PollingResource.DROPS, automatic=False, deadline=None
    )
    await gate.acquire(
        path="/drops", resource=PollingResource.DROPS, automatic=True, deadline=None
    )
    assert clock.elapsed == 60
    await gate.acquire(
        path="/drops", resource=PollingResource.DROPS, automatic=True, deadline=None
    )
    assert clock.elapsed == 120

    another = Admission(FakeClock())
    await another.acquire(path="/items", resource=None, automatic=False, deadline=None)
    await another.observe({"X-RateLimit-Limit-Minute": "2"})
    await another.acquire(path="/items", resource=None, automatic=False, deadline=None)
    assert another.clock.monotonic() == 30
    assert another._minute.remaining == 0
    await another.observe({"X-RateLimit-Remaining-Day": "0"})
    with pytest.raises(RateLimited, match="daily"):
        await another.acquire(
            path="/items", resource=None, automatic=False, deadline=None
        )


@pytest.mark.asyncio
async def test_reported_higher_quota_increases_allowance_without_restoring_spent_slots() -> (
    None
):
    clock = FakeClock()
    gate = Admission(clock)

    await gate.acquire(path="/items", resource=None, automatic=False, deadline=None)
    await gate.observe(
        {
            "X-RateLimit-Limit-Minute": "120",
            "X-RateLimit-Remaining-Minute": "119",
            "X-RateLimit-Limit-Day": "20000",
            "X-RateLimit-Remaining-Day": "19999",
        }
    )
    assert gate._minute.limit == 120
    assert gate._minute.remaining == 119
    assert gate._day.limit == 20_000
    assert gate._day.remaining == 19_999

    await gate.acquire(path="/items", resource=None, automatic=False, deadline=None)
    await gate.acquire(path="/items", resource=None, automatic=False, deadline=None)
    assert clock.elapsed == 1
    await gate.observe({"X-RateLimit-Remaining-Minute": "119"})
    assert gate._minute.remaining == 117


@pytest.mark.asyncio
async def test_higher_limit_needs_remaining_header_and_respects_ip_ceiling() -> None:
    gate = Admission(FakeClock())
    await gate.observe({"X-RateLimit-Limit-Minute": "20000"})
    assert gate._minute.limit == 60
    await gate.observe(
        {
            "X-RateLimit-Limit-Minute": "20000",
            "X-RateLimit-Remaining-Minute": "19999",
        }
    )
    assert gate._minute.limit == 10_000
    assert gate._minute.remaining == 10_000


@pytest.mark.asyncio
async def test_stale_quota_responses_cannot_restore_spent_concurrent_allowance() -> (
    None
):
    clock = FakeClock()
    gate = Admission(clock)
    await gate.observe(
        {"X-RateLimit-Limit-Minute": "2", "X-RateLimit-Remaining-Minute": "2"}
    )
    attempt_times: list[float] = []

    async def record_attempt() -> None:
        await gate.acquire(path="/items", resource=None, automatic=False, deadline=None)
        attempt_times.append(clock.monotonic())

    await asyncio.gather(record_attempt(), record_attempt())
    assert attempt_times == [0, 30]
    await gate.observe({"X-RateLimit-Remaining-Minute": "0"})
    await gate.observe({"X-RateLimit-Remaining-Minute": "1"})
    await record_attempt()
    assert attempt_times == [0, 30, 60]


@pytest.mark.asyncio
async def test_cooldown_expiry_does_not_release_a_request_burst() -> None:
    clock = FakeClock()
    gate = Admission(clock)
    await gate.cooldown(120)
    attempt_times: list[float] = []
    for _ in range(3):
        await gate.acquire(path="/items", resource=None, automatic=False, deadline=None)
        attempt_times.append(clock.monotonic())
    assert attempt_times == [120, 121, 122]


def test_retry_after_supports_http_dates_and_rejects_invalid_values() -> None:
    assert _retry_after("120", now=0) == 120
    assert _retry_after("Thu, 01 Jan 1970 00:02:00 GMT", now=0) == 120
    assert _retry_after("NaN", now=0) is None
    assert _retry_after("invalid", now=0) is None


@pytest.mark.asyncio
async def test_unknown_route_cannot_be_used_as_raw_http_escape_hatch() -> None:
    async with aiohttp.ClientSession() as session:
        client = Transport(
            session,
            "secret-token",
            origin=None,
            application=None,
            request=RequestConfig(),
            retry=RetryConfig(),
            admission=Admission(FakeClock()),
        )
        with pytest.raises(ConfigurationError, match="verified API registry"):
            await client.get(Route("/unverified"))


@pytest.mark.asyncio
async def test_client_close_cancels_pending_retry_and_keeps_borrowed_session_open() -> (
    None
):
    clock = BlockingClock()

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(status=503)

    async with local_server(handler) as url, aiohttp.ClientSession() as session:
        client = DankMemer("secret-token", session=session)
        await client.start()
        client._transport = transport(session, clock, url)
        pending = asyncio.create_task(client._request_json(ITEMS))
        await asyncio.wait_for(clock.waiting.wait(), timeout=2)
        await client.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert client.is_closed
        assert not session.closed
        with pytest.raises(LifecycleError):
            await client._request_json(ITEMS)
