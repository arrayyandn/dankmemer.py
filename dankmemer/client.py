import asyncio
import email.utils
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from dankmemer.exceptions import (
    BadRequestException,
    DankMemerConnectionException,
    DankMemerHTTPException,
    DankMemerResponseException,
    NotFoundException,
    RateLimitException,
    ServerErrorException,
)
from dankmemer.routes import (
    all,
    baits,
    buckets,
    creatures,
    decorations,
    events,
    items,
    locations,
    npcs,
    seasons,
    skills,
    skillsdata,
    stream,
    tanks,
    tools,
)

RETRYABLE_SERVER_STATUSES = {500, 502, 503, 504}
COLOR_CODES = {
    logging.INFO: "\033[94m",
    logging.WARNING: "\033[93m",
    logging.ERROR: "\033[1;91m",
}
RESET_CODE = "\033[0m"
DEFAULT_LOG_FORMAT = "[{asctime}] [{levelname:<7}] {name}: {message}"
DEFAULT_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
VALID_LOGGING_MODES = {"default", "null", "inherit"}


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color_code = COLOR_CODES.get(record.levelno, "")
        message = super().format(record)
        return f"{color_code}{message}{RESET_CODE}"


def _is_dankmemer_default_handler(handler: logging.Handler) -> bool:
    return bool(getattr(handler, "_dankmemer_default_handler", False))


def _is_dankmemer_null_handler(handler: logging.Handler) -> bool:
    return bool(getattr(handler, "_dankmemer_null_handler", False))


def _remove_package_handlers(package_logger: logging.Logger) -> None:
    for handler in list(package_logger.handlers):
        if _is_dankmemer_default_handler(handler) or _is_dankmemer_null_handler(
            handler
        ):
            package_logger.removeHandler(handler)


def _create_default_log_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(
        ColoredFormatter(
            DEFAULT_LOG_FORMAT,
            DEFAULT_LOG_DATE_FORMAT,
            style="{",
        )
    )
    handler._dankmemer_default_handler = True  # type: ignore[attr-defined]
    return handler


def _create_null_log_handler() -> logging.Handler:
    handler = logging.NullHandler()
    handler._dankmemer_null_handler = True  # type: ignore[attr-defined]
    return handler


def _configure_package_logger(logging_mode: str) -> logging.Logger:
    if logging_mode not in VALID_LOGGING_MODES:
        modes = ", ".join(sorted(VALID_LOGGING_MODES))
        raise ValueError(f"logging_mode must be one of: {modes}")

    package_logger = logging.getLogger("dankmemer")

    if logging_mode == "default":
        for handler in list(package_logger.handlers):
            if _is_dankmemer_null_handler(handler):
                package_logger.removeHandler(handler)
        if not any(
            _is_dankmemer_default_handler(handler)
            for handler in package_logger.handlers
        ):
            package_logger.addHandler(_create_default_log_handler())
        package_logger.setLevel(logging.INFO)
        package_logger.propagate = False
        return package_logger

    _remove_package_handlers(package_logger)

    if logging_mode == "null":
        if not any(
            _is_dankmemer_null_handler(handler) for handler in package_logger.handlers
        ):
            package_logger.addHandler(_create_null_log_handler())
        package_logger.propagate = False
        return package_logger

    package_logger.propagate = True
    return package_logger


class DankMemerClient:
    """
    An asynchronous client for accessing the DankAlert API.

    This client manages and provides access to various API endpoints
    (e.g. items, npcs, skills, tools) along with built-in caching. In addition, it supports
    anti-rate-limit behavior: when 'useAntirateLimit' is enabled (the default), the client
    ensures that no more than 10 requests are made every 10 seconds.

    Recommended usage:
      As a context manager:
        async with DankMemerClient(cache_ttl_hours=24) as client:
            items = await client.items.query()

      Without a context manager:
        client = DankMemerClient(cache_ttl_hours=24)
        items = await client.items.query()
        await client.close()

    If a session is passed in, the caller keeps ownership of that session and
    should close it separately.
    """

    def __init__(
        self,
        *,
        useAntirateLimit: bool = True,
        base_url: str = "https://api.dankalert.xyz/dank",
        session: aiohttp.ClientSession | None = None,
        cache_ttl_hours: float | None = 24,
        retry_attempts: int = 5,
        retry_backoff: float = 1.0,
        retry_max_sleep: float = 30.0,
        retry_on_rate_limit: bool | None = None,
        retry_on_server_error: bool = True,
        request_timeout: float | aiohttp.ClientTimeout | None = 30.0,
        logging_mode: str = "default",
        logger: logging.Logger | None = None,
    ) -> None:
        self.use_anti_ratelimit = useAntirateLimit
        self.base_url = base_url.rstrip("/")
        self.logger = logger or _configure_package_logger(logging_mode)
        self._session_owner = session is None
        self._request_timeout = self._normalize_timeout(request_timeout)
        self.session = session or aiohttp.ClientSession(timeout=self._request_timeout)

        if cache_ttl_hours is None or cache_ttl_hours == 0:
            self.cache_ttl = None
        elif cache_ttl_hours < 0:
            raise ValueError("cache_ttl_hours must be positive, 0, or None")
        else:
            self.cache_ttl = timedelta(hours=cache_ttl_hours)

        if retry_attempts < 1:
            raise ValueError("retry_attempts must be at least 1")
        if retry_backoff < 0:
            raise ValueError("retry_backoff cannot be negative")
        if retry_max_sleep < 0:
            raise ValueError("retry_max_sleep cannot be negative")

        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff
        self.retry_max_sleep = retry_max_sleep
        self.retry_on_rate_limit = (
            useAntirateLimit if retry_on_rate_limit is None else retry_on_rate_limit
        )
        self.retry_on_server_error = retry_on_server_error

        # Rate-limiting (current API: 10 requests per 10 seconds)
        # may be raised dynamically by Cloudflare even if our cap is respected.
        self._rate_limit_lock = asyncio.Lock()
        self.max_requests: int = 10
        self.request_period: float = 10.0
        self._request_times: list[float] = []

        self.items = items.ItemsRoute(self, self.cache_ttl)
        self.npcs = npcs.NPCsRoute(self, self.cache_ttl)
        self.baits = baits.BaitsRoute(self, self.cache_ttl)
        self.buckets = buckets.BucketsRoute(self, self.cache_ttl)
        self.creatures = creatures.CreaturesRoute(self, self.cache_ttl)
        self.decorations = decorations.DecorationsRoute(self, self.cache_ttl)
        self.events = events.EventsRoute(self, self.cache_ttl)
        self.locations = locations.LocationsRoute(self, self.cache_ttl)
        self.seasons = seasons.SeasonsRoute(self, self.cache_ttl)
        self.skills = skills.SkillsRoute(self, self.cache_ttl)
        self.skillsdata = skillsdata.SkillDataRoute(self, self.cache_ttl)
        self.stream = stream.StreamRoute(self, self.cache_ttl)
        self.tanks = tanks.TanksRoute(self, self.cache_ttl)
        self.tools = tools.ToolsRoute(self, self.cache_ttl)
        self.all = all.AllRoute(self, self.cache_ttl)
        self._routes = {
            "all": self.all,
            "baits": self.baits,
            "buckets": self.buckets,
            "creatures": self.creatures,
            "decorations": self.decorations,
            "events": self.events,
            "items": self.items,
            "locations": self.locations,
            "npcs": self.npcs,
            "seasons": self.seasons,
            "skills": self.skills,
            "skillsdata": self.skillsdata,
            "stream": self.stream,
            "tanks": self.tanks,
            "tools": self.tools,
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    @staticmethod
    def _normalize_timeout(
        timeout: float | aiohttp.ClientTimeout | None,
    ) -> aiohttp.ClientTimeout | None:
        if timeout is None or isinstance(timeout, aiohttp.ClientTimeout):
            return timeout
        if timeout <= 0:
            raise ValueError("request_timeout must be positive or None")
        return aiohttp.ClientTimeout(total=timeout)

    async def _wait_for_rate_limit(self) -> None:
        if not self.use_anti_ratelimit:
            return

        async with self._rate_limit_lock:
            now = time.monotonic()
            self._request_times = [
                t for t in self._request_times if now - t < self.request_period
            ]
            while len(self._request_times) >= self.max_requests:
                wait_time = self.request_period - (now - self._request_times[0])
                wait_time = max(0.0, wait_time)
                self.logger.debug(
                    "Internal rate limit reached; waiting %.2f seconds",
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                now = time.monotonic()
                self._request_times = [
                    t for t in self._request_times if now - t < self.request_period
                ]
            self._request_times.append(now)

    def _parse_retry_after(self, retry_after: str | None) -> float | None:
        if retry_after is None:
            return None
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass

        try:
            retry_at = email.utils.parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())

    def _retry_sleep_for(
        self,
        attempt: int,
        retry_after: str | None = None,
    ) -> float:
        parsed_retry_after = self._parse_retry_after(retry_after)
        if parsed_retry_after is not None:
            return min(parsed_retry_after, self.retry_max_sleep)

        sleep_time = min(
            self.retry_backoff * (2 ** (attempt - 1)),
            self.retry_max_sleep,
        )
        if sleep_time <= 0:
            return 0.0
        return min(sleep_time * random.uniform(0.8, 1.2), self.retry_max_sleep)

    async def _sleep_before_retry(
        self,
        route: str,
        attempt: int,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        sleep_time = self._retry_sleep_for(attempt, retry_after)
        self.logger.warning(
            "Retrying route %s after %s on attempt %d/%d; sleeping %.2f seconds",
            route,
            f"HTTP {status_code}" if status_code else "request error",
            attempt,
            self.retry_attempts,
            sleep_time,
        )
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    async def _handle_response(self, route: str, response: aiohttp.ClientResponse):
        if response.status == 404:
            raise NotFoundException(
                f"Resource not found at route: {route}",
                status_code=404,
                route=route,
            )
        if response.status == 400:
            raise BadRequestException(
                f"Bad request for route: {route}",
                status_code=400,
                route=route,
            )
        if response.status == 429:
            raise RateLimitException(
                f"Rate limit hit for route: {route}",
                status_code=429,
                route=route,
            )
        if 500 <= response.status < 600:
            raise ServerErrorException(
                f"Server error at route: {route}",
                status_code=response.status,
                route=route,
            )
        if response.status >= 400:
            raise DankMemerHTTPException(
                f"HTTP error {response.status} at route: {route}",
                status_code=response.status,
                route=route,
            )

        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError) as exc:
            raise DankMemerResponseException(
                f"Invalid JSON response from route: {route}",
                status_code=response.status,
                route=route,
            ) from exc

    def _should_retry_response(self, status_code: int) -> bool:
        if status_code == 429:
            return self.retry_on_rate_limit
        return self.retry_on_server_error and status_code in RETRYABLE_SERVER_STATUSES

    async def request(self, route: str, params: dict[str, Any] | None = None):
        """
        Makes an HTTP GET request to the specified route with the given query parameters,
        while enforcing the API's rate limiting if useAntirateLimit is enabled.

        :param route: The API route to request (appended to the base URL).
        :param params: Optional dictionary of query parameters.
        :return: The parsed JSON response.
        """

        await self._wait_for_rate_limit()
        url = f"{self.base_url}/{route}"

        for attempt in range(1, self.retry_attempts + 1):
            try:
                async with self.session.get(
                    url,
                    params=params,
                    timeout=self._request_timeout,
                ) as response:
                    if (
                        attempt < self.retry_attempts
                        and self._should_retry_response(response.status)
                    ):
                        await self._sleep_before_retry(
                            route,
                            attempt,
                            status_code=response.status,
                            retry_after=response.headers.get("Retry-After"),
                        )
                        continue
                    return await self._handle_response(route, response)

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self.retry_attempts:
                    await self._sleep_before_retry(route, attempt)
                    continue
                raise DankMemerConnectionException(
                    f"Request failed for route: {route}",
                    route=route,
                ) from exc

        raise DankMemerHTTPException(f"Request failed for route: {route}", route=route)

    def clear_cache(self) -> None:
        """Clear all route caches for this client."""
        for route in self._routes.values():
            route.clear_cache()

    def clear_route_cache(self, route_name: str) -> None:
        """Clear one route cache by client attribute name, such as ``items``."""
        try:
            route = self._routes[route_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._routes))
            raise ValueError(
                f"Unknown route cache {route_name!r}. Available routes: {available}"
            ) from exc
        route.clear_cache()

    def cache_info(self) -> dict[str, dict[str, Any]]:
        """Return cache state for all routes."""
        return {name: route.cache_info() for name, route in self._routes.items()}

    async def close(self) -> None:
        if (
            self._session_owner
            and self.session
            and not self.session.closed
        ):
            await self.session.close()
