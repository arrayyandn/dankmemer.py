from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """Time source shared by request admission, caching, and event polling.

    ``monotonic`` and ``sleep_until`` use the same deadline clock. ``sleep``
    takes a relative delay. ``time`` returns epoch seconds for rate-limit
    resets and HTTP dates.
    """

    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...

    async def sleep_until(self, deadline: float) -> None: ...


class SystemClock:
    """Use the running event loop for deadlines and system time for epochs."""

    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    def time(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def sleep_until(self, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - self.monotonic()))
