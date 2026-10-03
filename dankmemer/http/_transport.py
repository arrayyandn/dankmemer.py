from __future__ import annotations

import asyncio
import json
import math
import random
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import cast

import aiohttp

from dankmemer._version import __version__
from dankmemer.config import ApplicationIdentity, RequestConfig, RetryConfig
from dankmemer.errors import (
    AuthenticationError,
    BadRequest,
    ConfigurationError,
    DankMemerConnectionError,
    DankMemerHTTPError,
    DankMemerResponseError,
    DankMemerTimeoutError,
    Forbidden,
    LifecycleError,
    NotFound,
    RateLimited,
    ServerError,
)

from ._admission import Admission
from ._routes import Route, check_route

_API_BASE_URL = "https://dankmemer.lol/api/v1"


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    value: object
    retry_after: float | None


def _retry_after(value: str | None, *, now: float) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = date.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _http_error(
    status: int, path: str, retry_after: float | None
) -> DankMemerHTTPError:
    error_type: type[DankMemerHTTPError]
    if status == 400:
        error_type = BadRequest
    elif status == 401:
        error_type = AuthenticationError
    elif status == 403:
        error_type = Forbidden
    elif status == 404:
        error_type = NotFound
    elif status == 429:
        error_type = RateLimited
    elif 500 <= status <= 599:
        error_type = ServerError
    else:
        error_type = DankMemerHTTPError
    return error_type(
        f"official API returned HTTP {status} for {path}",
        status_code=status,
        path=path,
        retry_after_seconds=retry_after,
    )


def _validate_session(session: aiohttp.ClientSession) -> None:
    if session.closed:
        raise ConfigurationError("supplied session is closed")
    if getattr(session, "_base_url", None) is not None:
        raise ConfigurationError("supplied session must not set a base URL")
    if getattr(session, "_default_auth", None) is not None:
        raise ConfigurationError("supplied session must not set default authentication")
    if getattr(session, "_raise_for_status", False):
        raise ConfigurationError(
            "supplied session must not raise for status automatically"
        )
    if "Authorization" in session.headers or "Origin" in session.headers:
        raise ConfigurationError("supplied session has conflicting default headers")


class Transport:
    """Send authenticated GET requests to the Dank Memer API.

    Every attempt, including a retry, consumes request admission. Successful
    JSON bodies are returned for parsing by resource methods; HTTP 204 and
    205 return ``None``. Redirects are not followed.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        *,
        origin: str | None,
        application: ApplicationIdentity | None,
        request: RequestConfig,
        retry: RetryConfig,
        admission: Admission,
    ) -> None:
        _validate_session(session)
        self._session = session
        self._base_url = _API_BASE_URL
        self._token = token
        self._origin = origin
        self._application = application
        self._request = request
        self._retry = retry
        self._admission = admission
        self._closed = False
        self._active: set[asyncio.Task[object]] = set()
        self._completions: set[asyncio.Future[None]] = set()

    @property
    def request_completions(self) -> tuple[asyncio.Future[None], ...]:
        return tuple(self._completions)

    async def close(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        active = [task for task in self._active if task is not current]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._token = ""

    def _headers(self) -> dict[str, str]:
        marker = f"dankmemer.py/{__version__}"
        if self._application is not None:
            prefix = self._application.name
            if self._application.version is not None:
                prefix += f"/{self._application.version}"
            marker = f"{prefix} {marker}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": marker,
            "Accept": "application/json",
            # Both encodings work without optional compression dependencies.
            "Accept-Encoding": "gzip, deflate",
        }
        if self._origin is not None:
            headers["Origin"] = self._origin
        return headers

    async def _read_json(self, response: aiohttp.ClientResponse, path: str) -> object:
        maximum = self._request.max_response_bytes
        if response.content_length is not None and response.content_length > maximum:
            raise DankMemerResponseError("response exceeds byte limit", path=path)
        # Content-Length can be absent or wrong, so enforce the limit while reading.
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(16_384):
            size += len(chunk)
            if size > maximum:
                raise DankMemerResponseError("response exceeds byte limit", path=path)
            chunks.append(chunk)
        if not chunks:
            raise DankMemerResponseError(
                "successful response has no JSON body", path=path
            )
        payload = b"".join(chunks)
        chunks.clear()
        try:
            parsed: object = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            payload = b""
        else:
            return parsed
        raise DankMemerResponseError("response is not valid JSON", path=path)

    async def _attempt(
        self,
        path: str,
        query: dict[str, int | str],
        safe_path: str,
        timeout_seconds: float | None,
    ) -> _Response:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        # Do not follow redirects when sending the developer token.
        async with self._session.get(
            f"{self._base_url}{path}",
            params=query,
            headers=self._headers(),
            allow_redirects=False,
            timeout=timeout,
        ) as response:
            await self._admission.observe(response.headers)
            retry_after = _retry_after(
                response.headers.get("Retry-After"), now=self._admission.clock.time()
            )
            if response.status in (204, 205):
                return _Response(response.status, None, retry_after)
            if 200 <= response.status <= 299:
                value = await self._read_json(response, safe_path)
                return _Response(response.status, value, retry_after)
            return _Response(response.status, None, retry_after)

    def _backoff(self, retry_index: int, *, service_unavailable: bool) -> float:
        base: float = min(
            self._retry.max_backoff_seconds,
            self._retry.initial_backoff_seconds * (2.0 ** min(retry_index, 30)),
        )
        if service_unavailable:
            base = max(5.0, base)
        return base + random.random() * base * self._retry.jitter_ratio

    async def get(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        if self._closed:
            raise LifecycleError("client transport is closed")
        task = asyncio.current_task()
        assert task is not None
        tracked = cast(asyncio.Task[object], task)
        completion: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._active.add(tracked)
        self._completions.add(completion)
        try:
            return await self._get(
                route,
                automatic=automatic,
                user_id=user_id,
                cursor=cursor,
                limit=limit,
                category=category,
            )
        finally:
            self._active.discard(tracked)
            self._completions.discard(completion)
            completion.set_result(None)

    async def _get(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        check_route(route)
        if automatic and route.event_resource is None:
            raise ConfigurationError("automatic requests require an event resource")
        path, query = route.target(
            user_id=user_id, cursor=cursor, limit=limit, category=category
        )
        # Use the template in errors so user IDs do not appear in logs.
        safe_path = route.template
        total = self._request.total_timeout_seconds
        deadline = None if total is None else self._admission.clock.monotonic() + total

        for index in range(self._retry.max_attempts):
            # Each retry is another request and must pass admission again.
            await self._admission.acquire(
                path=safe_path,
                resource=route.event_resource,
                automatic=automatic,
                deadline=deadline,
            )
            remaining = (
                None
                if deadline is None
                else deadline - self._admission.clock.monotonic()
            )
            if remaining is not None and remaining <= 0:
                raise DankMemerTimeoutError("request deadline expired", path=safe_path)
            attempt_timeout = self._request.timeout_seconds
            if remaining is not None:
                attempt_timeout = (
                    remaining
                    if attempt_timeout is None
                    else min(attempt_timeout, remaining)
                )

            network_error: DankMemerConnectionError | None = None
            response: _Response | None = None
            try:
                response = await self._attempt(path, query, safe_path, attempt_timeout)
            except TimeoutError:
                network_error = DankMemerTimeoutError(
                    "request timed out", path=safe_path
                )
            except aiohttp.ClientError:
                network_error = DankMemerConnectionError(
                    "connection to official API failed", path=safe_path
                )

            if network_error is not None:
                if index + 1 == self._retry.max_attempts:
                    raise network_error
                delay = self._backoff(index, service_unavailable=False)
                if (
                    deadline is not None
                    and self._admission.clock.monotonic() + delay >= deadline
                ):
                    raise network_error
                await self._admission.clock.sleep(delay)
                continue

            assert response is not None
            if 200 <= response.status <= 299:
                return response.value
            error = _http_error(response.status, safe_path, response.retry_after)
            retryable = (
                response.status == 429 and self._retry.retry_on_rate_limit
            ) or (500 <= response.status <= 599 and self._retry.retry_on_server_error)
            delay = self._backoff(index, service_unavailable=response.status == 503)
            if response.status in (429, 503):
                delay = max(delay, response.retry_after or 0.0)
                await self._admission.cooldown(delay)
            if not retryable or index + 1 == self._retry.max_attempts:
                raise error
            if (
                deadline is not None
                and self._admission.clock.monotonic() + delay >= deadline
            ):
                raise error
            await self._admission.clock.sleep(delay)

        raise AssertionError("retry loop exhausted without a result")
