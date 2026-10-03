# pyright: reportPrivateUsage=false

import asyncio
import logging
from collections.abc import Callable, MutableMapping
from datetime import timedelta
from enum import Enum
from types import MappingProxyType
from typing import cast

import aiohttp
import pytest

from dankmemer._clock import SystemClock
from dankmemer._enum import _Enum
from dankmemer.client import DankMemer
from dankmemer.config import (
    ApplicationIdentity,
    CacheConfig,
    DailyPolling,
    DisabledPolling,
    HourlyPolling,
    IntervalPolling,
    PollingConfig,
    RequestConfig,
    RetryConfig,
    TTLCache,
)
from dankmemer.enums import ClientState, EventDelivery, PollingResource
from dankmemer.errors import ConfigurationError, LifecycleError


def test_system_clock_uses_running_loop_time(monkeypatch: pytest.MonkeyPatch) -> None:
    class LoopWithKnownTime:
        def time(self) -> float:
            return 123.5

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "get_running_loop", lambda: LoopWithKnownTime())
        assert SystemClock().monotonic() == 123.5


def _assert_configuration_error(
    factory: Callable[..., object], /, **kwargs: object
) -> None:
    with pytest.raises(ConfigurationError):
        factory(**kwargs)


def _assert_immutable(target: object, name: str, value: object) -> None:
    with pytest.raises(TypeError):
        setattr(target, name, value)


def test_runtime_enums_are_canonical_typed_and_immutable() -> None:
    assert isinstance(ClientState.RUNNING, ClientState)
    assert not isinstance(ClientState.RUNNING, Enum)
    assert ClientState("running") is ClientState.RUNNING
    assert ClientState["RUNNING"] is ClientState.RUNNING
    assert list(ClientState) == [
        ClientState.NEW,
        ClientState.STARTING,
        ClientState.RUNNING,
        ClientState.CLOSING,
        ClientState.CLOSED,
    ]
    assert len(ClientState) == 5
    assert isinstance(ClientState.__members__, MappingProxyType)
    assert ClientState.RUNNING.name == "RUNNING"
    assert ClientState.RUNNING.value == "running"
    assert str(ClientState.RUNNING) == "running"
    assert "ClientState.RUNNING" in repr(ClientState.RUNNING)
    assert ClientState.RUNNING != "running"
    assert ClientState.RUNNING != PollingResource.DROPS
    lookup: dict[ClientState, str] = {ClientState.RUNNING: "ok"}
    assert lookup[ClientState("running")] == "ok"
    assert EventDelivery.DURABLE.value == "durable"

    _assert_immutable(ClientState.RUNNING, "value", "changed")
    _assert_immutable(ClientState.RUNNING, "_name", "CHANGED")
    _assert_immutable(ClientState, "RUNNING", "changed")
    with pytest.raises(TypeError):
        delattr(ClientState, "RUNNING")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ClientState], ClientState.__members__)["OTHER"] = (
            ClientState.NEW
        )
    with pytest.raises(ValueError):
        ClientState("missing")
    with pytest.raises(KeyError):
        ClientState["MISSING"]


def test_enum_definitions_reject_duplicate_values_and_subclassing() -> None:
    with pytest.raises(ValueError):
        type("Duplicate", (_Enum,), {"FIRST": "same", "SECOND": "same"})

    with pytest.raises(TypeError):
        type("Extended", (ClientState,), {"EXTRA": "extra"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"seconds": 60, "minutes": 1},
        {"seconds": 59.9},
        {"seconds": True},
        {"minutes": 0},
        {"hours": float("nan")},
        {"hours": float("inf")},
    ],
)
def test_interval_policy_rejects_unsafe_values(kwargs: dict[str, object]) -> None:
    _assert_configuration_error(IntervalPolling, **kwargs)


def test_calendar_policies_and_resource_slots_are_validated() -> None:
    assert IntervalPolling(minutes=1).interval == timedelta(seconds=60)
    assert IntervalPolling(hours=1).interval == timedelta(hours=1)
    assert (
        PollingConfig(
            lottery=HourlyPolling(),
            merchant_trades=DailyPolling(),
            store_daily_gifts=DailyPolling(),
            stream_trending_game=DailyPolling(),
            blogs=DisabledPolling(),
        ).lottery
        == HourlyPolling()
    )

    for invalid in (
        {"retry_seconds": 59},
        {"reconcile_minutes": 0.5},
        {"offset_seconds": 3600},
    ):
        _assert_configuration_error(HourlyPolling, **invalid)
    _assert_configuration_error(DailyPolling, offset_seconds=86400)
    _assert_configuration_error(PollingConfig, drops=HourlyPolling())
    _assert_configuration_error(PollingConfig, merchant_trades=HourlyPolling())
    _assert_configuration_error(PollingConfig, store_daily_gifts=HourlyPolling())
    _assert_configuration_error(PollingConfig, stream_trending_game=HourlyPolling())
    _assert_configuration_error(PollingConfig, startup_jitter_seconds=-1)


def test_request_retry_cache_and_identity_configuration() -> None:
    assert RequestConfig().max_response_bytes == 10_485_760
    assert RetryConfig().max_attempts == 3

    _assert_configuration_error(RequestConfig, max_response_bytes=True)
    _assert_configuration_error(RequestConfig, timeout_seconds=0)
    _assert_configuration_error(RequestConfig, total_timeout_seconds=float("inf"))
    _assert_configuration_error(RetryConfig, max_attempts=0)
    _assert_configuration_error(
        RetryConfig, initial_backoff_seconds=10, max_backoff_seconds=5
    )
    _assert_configuration_error(RetryConfig, jitter_ratio=1.1)
    _assert_configuration_error(RetryConfig, retry_on_server_error=1)
    _assert_configuration_error(TTLCache, ttl=timedelta(0))
    _assert_configuration_error(CacheConfig, items="forever")
    _assert_configuration_error(ApplicationIdentity, name="injected\r\nHeader: value")


def test_client_constructor_validates_without_allocating_session() -> None:
    client = DankMemer("test-token", origin="https://example.test:443")
    assert client.state is ClientState.NEW
    assert client._session is None
    assert client.is_closed is False
    assert client._logger is logging.getLogger("dankmemer")
    assert any(
        isinstance(handler, logging.NullHandler)
        for handler in logging.getLogger("dankmemer").handlers
    )

    for invalid in (
        {"token": ""},
        {"token": "injected\nvalue"},
        {"token": "test", "origin": "http://example.test"},
        {"token": "test", "origin": "https://example.test/path"},
        {"token": "test", "origin": "https://user@example.test"},
        {"token": "test", "origin": "https://example.test:0"},
        {"token": "test", "origin": "https://example.test:"},
        {"token": "test", "origin": "https://example.test:99999"},
        {"token": "test", "request": object()},
        {"token": "test", "silent": None},
        {"token": "test", "silent": True, "logger": logging.getLogger("test")},
    ):
        _assert_configuration_error(DankMemer, **invalid)


@pytest.mark.asyncio
async def test_client_start_and_lazy_transport_share_one_owned_session() -> None:
    client = DankMemer("test-token")
    lazy, _, _ = await asyncio.gather(
        client._ensure_session(), client.start(), client.start()
    )
    assert client.state is ClientState.RUNNING
    assert client.is_running
    assert client._session is lazy
    assert not lazy.closed

    await asyncio.gather(client.close(), client.close())
    assert client.is_closed
    assert lazy.closed
    await client.close()
    with pytest.raises(LifecycleError):
        await client.start()
    with pytest.raises(LifecycleError):
        await client._ensure_session()


@pytest.mark.asyncio
async def test_lazy_transport_does_not_start_background_lifecycle() -> None:
    client = DankMemer("test-token")
    session = await client._ensure_session()
    assert client.state is ClientState.NEW
    await client.start()
    assert client._session is session
    await client.close()


@pytest.mark.asyncio
async def test_borrowed_session_stays_open_and_closed_session_is_rejected() -> None:
    async with aiohttp.ClientSession() as session:
        client = DankMemer("test-token", session=session)
        await client.start()
        await client.close()
        assert not session.closed

    with pytest.raises(LifecycleError):
        await DankMemer("test-token", session=session).start()


@pytest.mark.asyncio
async def test_cancelled_close_still_finishes_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DankMemer("test-token")
    await client.start()
    session = client._session
    assert session is not None
    original_close = session.close
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_close() -> None:
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(session, "close", slow_close)
    closing = asyncio.create_task(client.close())
    await entered.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert client.state is ClientState.CLOSING
    release.set()
    await client.close()
    assert client.is_closed
    assert session.closed


@pytest.mark.asyncio
async def test_context_closes_owned_session_after_application_error() -> None:
    client = DankMemer("test-token")
    session: aiohttp.ClientSession | None = None
    with pytest.raises(ZeroDivisionError):
        async with client:
            assert client.is_running
            session = client._session
            assert session is not None
            raise ZeroDivisionError
    assert client.is_closed
    assert session is not None
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_context_entry_cleans_up_and_preserves_startup_error(
    monkeypatch: pytest.MonkeyPatch,
    owned: bool,
    error_type: type[BaseException],
) -> None:
    async with aiohttp.ClientSession() as supplied:
        dank = DankMemer("test-token", session=None if owned else supplied)
        error = error_type("startup interrupted")
        sessions: list[aiohttp.ClientSession] = []

        async def fail_start() -> None:
            assert dank._session is not None
            sessions.append(dank._session)
            raise error

        monkeypatch.setattr(dank._events, "start", fail_start)
        try:
            with pytest.raises(error_type) as raised:
                async with dank:
                    raise AssertionError("context body must not run")
            assert raised.value is error
            assert dank.is_closed
            assert len(sessions) == 1
            assert sessions[0].closed is owned
            assert not supplied.closed
        finally:
            await dank.close()


@pytest.mark.asyncio
async def test_failed_context_entry_keeps_startup_error_when_cleanup_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dank = DankMemer("test-token")
    original_close = dank.close

    async def fail_start() -> None:
        raise ValueError("startup failed")

    async def fail_after_close() -> None:
        await original_close()
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(dank._events, "start", fail_start)
    monkeypatch.setattr(dank, "close", fail_after_close)
    with caplog.at_level(logging.ERROR, logger="dankmemer"):
        with pytest.raises(ValueError, match="startup failed"):
            async with dank:
                raise AssertionError("context body must not run")
    assert dank.is_closed
    assert any(
        record.message == "client cleanup failed after a startup error"
        and record.exc_info is not None
        and record.exc_info[0] is RuntimeError
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_context_keeps_body_error_and_logs_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with aiohttp.ClientSession() as session:
        logger = logging.getLogger("dankmemer.test.cleanup")
        client = DankMemer("test-token", session=session, logger=logger)

        async def fail_close() -> None:
            raise RuntimeError("cleanup failed")

        monkeypatch.setattr(client, "close", fail_close)
        with caplog.at_level(logging.ERROR, logger=logger.name):
            with pytest.raises(ValueError, match="application failed"):
                async with client:
                    raise ValueError("application failed")

        records = [
            record
            for record in caplog.records
            if record.name == logger.name
            and record.message == "client cleanup failed after an application error"
        ]
        assert len(records) == 1
        assert records[0].exc_info is not None
        assert records[0].exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_silent_client_keeps_body_error_without_logging_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with aiohttp.ClientSession() as session:
        client = DankMemer("test-token", session=session, silent=True)

        async def fail_close() -> None:
            raise RuntimeError("cleanup failed")

        monkeypatch.setattr(client, "close", fail_close)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ValueError, match="application failed"):
                async with client:
                    raise ValueError("application failed")

        assert not [
            record for record in caplog.records if record.name == "dankmemer.silent"
        ]


@pytest.mark.asyncio
async def test_client_rejects_other_event_loop_after_binding() -> None:
    client = DankMemer("test-token")
    await client.start()

    async def other_loop() -> None:
        with pytest.raises(LifecycleError):
            await client.start()

    await asyncio.to_thread(lambda: asyncio.run(other_loop()))
    await client.close()
