from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from dankmemer._clock import Clock as _Clock
from dankmemer._clock import SystemClock as _SystemClock
from dankmemer.enums import PollingResource

from ._budget import RequestBudget


class SharedAdmission(Protocol):
    async def acquire(
        self,
        *,
        path: str,
        resource: PollingResource | None,
        automatic: bool,
        deadline: float | None,
    ) -> None: ...
    async def observe(self, headers: Mapping[str, str]) -> None: ...
    async def cooldown(self, seconds: float) -> None: ...


class Admission(RequestBudget):
    """Pace request attempts against local quotas and server cooldowns.

    The API's default application quotas are 60 requests per minute and
    10,000 per day, shared across keys. This limiter reserves one slot per
    request attempt, including retries. It begins with those defaults and
    adjusts them from response headers, up to the API's per-IP minute ceiling.
    Request attempts are spaced according to the reported minute limit,
    starting at one second apart. A request to an event resource postpones
    its next automatic attempt until at least 60 seconds after that attempt.
    """

    def __init__(self, clock: _Clock | None = None) -> None:
        self._clock = clock if clock is not None else _SystemClock()
        super().__init__(self._clock.time())
        self._lock = asyncio.Lock()
        self.shared: SharedAdmission | None = None

    @property
    def clock(self) -> _Clock:
        return self._clock

    async def acquire(
        self,
        *,
        path: str,
        resource: PollingResource | None,
        automatic: bool,
        deadline: float | None,
    ) -> None:
        """Reserve one request attempt, waiting if a local limit requires it.

        Raise :class:`~dankmemer.errors.RateLimited` if the daily allowance is
        exhausted, or :class:`~dankmemer.errors.DankMemerTimeoutError` if the
        next permitted request attempt would reach or exceed ``deadline``.
        """
        if self.shared is not None:
            await self.shared.acquire(
                path=path, resource=resource, automatic=automatic, deadline=deadline
            )
            return
        while True:
            async with self._lock:
                wait = self.reserve(
                    self._clock.monotonic(),
                    self._clock.time(),
                    path=path,
                    resource=resource,
                    automatic=automatic,
                    deadline=deadline,
                )
                if wait == 0:
                    return
            await self._clock.sleep(wait)

    async def observe(self, headers: Mapping[str, str]) -> None:
        """Update local budgets using rate-limit response headers."""
        if self.shared is not None:
            await self.shared.observe(headers)
            return
        async with self._lock:
            self.update_headers(self._clock.time(), headers)

    async def cooldown(self, seconds: float) -> None:
        """Delay subsequent requests after a rate-limit or service error."""
        if self.shared is not None:
            await self.shared.cooldown(seconds)
            return
        async with self._lock:
            self.delay(self._clock.monotonic() + seconds)
