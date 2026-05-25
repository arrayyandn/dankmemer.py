import logging

import aiohttp
import pytest

from dankmemer import DankMemerClient
from dankmemer.exceptions import (
    DankMemerConnectionException,
    DankMemerResponseException,
    RateLimitException,
)


class DummyResponse:
    def __init__(self, status, payload=None, headers=None, json_error=None):
        self.status = status
        self.payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class DummySession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.closed = False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        self.closed = True


def _remove_dankmemer_test_handlers() -> None:
    package_logger = logging.getLogger("dankmemer")
    for handler in list(package_logger.handlers):
        if getattr(handler, "_dankmemer_default_handler", False) or getattr(
            handler, "_dankmemer_null_handler", False
        ):
            package_logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def clean_package_logger():
    package_logger = logging.getLogger("dankmemer")
    original_level = package_logger.level
    original_propagate = package_logger.propagate
    _remove_dankmemer_test_handlers()
    yield
    _remove_dankmemer_test_handlers()
    package_logger.setLevel(original_level)
    package_logger.propagate = original_propagate


@pytest.mark.asyncio
async def test_retries_temporary_server_error():
    session = DummySession(
        [
            DummyResponse(503),
            DummyResponse(200, {"ok": True}),
        ]
    )
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        retry_attempts=2,
        retry_backoff=0,
        logging_mode="null",
    )

    result = await client.request("items")
    await client.close()

    assert result == {"ok": True}
    assert len(session.requests) == 2
    assert session.closed is False


@pytest.mark.asyncio
async def test_retries_rate_limit_with_retry_after():
    session = DummySession(
        [
            DummyResponse(429, headers={"Retry-After": "0"}),
            DummyResponse(200, {"ok": True}),
        ]
    )
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        retry_attempts=2,
        retry_backoff=0,
        retry_on_rate_limit=True,
        logging_mode="null",
    )

    result = await client.request("items")

    assert result == {"ok": True}
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_rate_limit_can_skip_retry():
    session = DummySession([DummyResponse(429, headers={"Retry-After": "0"})])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        retry_attempts=2,
        retry_on_rate_limit=False,
        logging_mode="null",
    )

    with pytest.raises(RateLimitException) as exc_info:
        await client.request("items")

    assert exc_info.value.status_code == 429
    assert exc_info.value.route == "items"
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_connection_errors_are_wrapped():
    session = DummySession([aiohttp.ClientConnectionError("offline")])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        retry_attempts=1,
        logging_mode="null",
    )

    with pytest.raises(DankMemerConnectionException) as exc_info:
        await client.request("items")

    assert exc_info.value.route == "items"


@pytest.mark.asyncio
async def test_invalid_json_is_wrapped():
    session = DummySession([DummyResponse(200, json_error=ValueError("bad json"))])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        logging_mode="null",
    )

    with pytest.raises(DankMemerResponseException) as exc_info:
        await client.request("items")

    assert exc_info.value.status_code == 200
    assert exc_info.value.route == "items"


@pytest.mark.asyncio
async def test_route_and_client_cache_controls():
    payload = {
        "trending_game": {
            "lastModified": "2026-05-25T18:18:07.690810+00:00",
            "name": "Among Us",
        }
    }
    requests = []

    async def dummy_request(route: str, params: dict | None = None):
        requests.append(route)
        return payload

    async with DankMemerClient(cache_ttl_hours=1, logging_mode="null") as client:
        client.request = dummy_request

        await client.stream.query()
        await client.stream.query()
        assert requests == ["stream"]
        assert client.stream.cache_info()["has_value"] is True

        client.stream.clear_cache()
        await client.stream.query()
        assert requests == ["stream", "stream"]

        client.clear_route_cache("stream")
        assert client.stream.cache_info()["has_value"] is False

        await client.stream.query()
        client.clear_cache()
        assert client.cache_info()["stream"]["has_value"] is False


@pytest.mark.asyncio
async def test_cache_can_be_disabled():
    payload = {
        "trending_game": {
            "lastModified": "2026-05-25T18:18:07.690810+00:00",
            "name": "Among Us",
        }
    }
    requests = []

    async def dummy_request(route: str, params: dict | None = None):
        requests.append(route)
        return payload

    async with DankMemerClient(cache_ttl_hours=0, logging_mode="null") as client:
        client.request = dummy_request

        await client.stream.query()
        await client.stream.query()

        assert requests == ["stream", "stream"]
        assert client.stream.cache_info()["enabled"] is False
        assert client.stream.cache_info()["has_value"] is False


def test_importing_client_does_not_attach_default_stream_handler():
    package_logger = logging.getLogger("dankmemer")

    assert not any(
        getattr(handler, "_dankmemer_default_handler", False)
        for handler in package_logger.handlers
    )


def test_default_logging_mode_attaches_colored_handler():
    package_logger = logging.getLogger("dankmemer")

    client = DankMemerClient(session=DummySession([]))

    assert client.logger is package_logger
    assert sum(
        1
        for handler in package_logger.handlers
        if getattr(handler, "_dankmemer_default_handler", False)
    ) == 1
    assert package_logger.level == logging.INFO


def test_explicit_default_logging_mode_does_not_duplicate_handlers():
    package_logger = logging.getLogger("dankmemer")

    first = DankMemerClient(session=DummySession([]), logging_mode="default")
    second = DankMemerClient(session=DummySession([]), logging_mode="default")

    assert first.logger is package_logger
    assert second.logger is package_logger
    assert sum(
        1
        for handler in package_logger.handlers
        if getattr(handler, "_dankmemer_default_handler", False)
    ) == 1


def test_null_logging_mode_is_silent_and_has_no_default_handler():
    package_logger = logging.getLogger("dankmemer")
    DankMemerClient(session=DummySession([]), logging_mode="default")

    client = DankMemerClient(session=DummySession([]), logging_mode="null")
    another = DankMemerClient(session=DummySession([]), logging_mode="null")

    assert client.logger is package_logger
    assert another.logger is package_logger
    assert not any(
        getattr(handler, "_dankmemer_default_handler", False)
        for handler in package_logger.handlers
    )
    assert sum(
        1
        for handler in package_logger.handlers
        if getattr(handler, "_dankmemer_null_handler", False)
    ) == 1
    assert package_logger.propagate is False


def test_inherit_logging_mode_uses_parent_logging():
    package_logger = logging.getLogger("dankmemer")
    DankMemerClient(session=DummySession([]), logging_mode="default")

    client = DankMemerClient(session=DummySession([]), logging_mode="inherit")

    assert client.logger is package_logger
    assert not any(
        getattr(handler, "_dankmemer_default_handler", False)
        or getattr(handler, "_dankmemer_null_handler", False)
        for handler in package_logger.handlers
    )
    assert package_logger.propagate is True


def test_custom_logger_wins_and_is_not_mutated():
    package_logger = logging.getLogger("dankmemer")
    package_handlers = list(package_logger.handlers)
    custom_logger = logging.getLogger("tests.dankmemer.custom")
    custom_handler = logging.NullHandler()
    custom_logger.addHandler(custom_handler)
    custom_level = custom_logger.level
    custom_propagate = custom_logger.propagate

    try:
        client = DankMemerClient(
            session=DummySession([]),
            logger=custom_logger,
            logging_mode="not-a-real-mode",
        )
        assert client.logger is custom_logger
        assert custom_logger.handlers[-1] is custom_handler
        assert custom_logger.level == custom_level
        assert custom_logger.propagate == custom_propagate
        assert package_logger.handlers == package_handlers
    finally:
        custom_logger.removeHandler(custom_handler)


def test_invalid_logging_mode_raises_value_error():
    with pytest.raises(ValueError):
        DankMemerClient(session=DummySession([]), logging_mode="verbose")


def test_retry_backoff_jitter_is_capped(monkeypatch):
    client = DankMemerClient(
        session=DummySession([]),
        retry_backoff=10,
        retry_max_sleep=1,
        logging_mode="null",
    )
    monkeypatch.setattr("dankmemer.client.random.uniform", lambda _low, _high: 1.2)

    assert client._retry_sleep_for(1) == 1


@pytest.mark.asyncio
async def test_session_ownership_paths_are_safe():
    user_session = DummySession([])
    client = DankMemerClient(session=user_session, logging_mode="null")
    await client.close()
    await client.close()
    assert user_session.closed is False

    own_client = DankMemerClient(logging_mode="null")
    owned_session = own_client.session
    await own_client.close()
    await own_client.close()
    assert owned_session.closed is True
