from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias, TypeVar, cast

from ._clock import Clock, SystemClock
from .config import CachePolicy, NoCache, TTLCache
from .errors import LifecycleError

CacheKey: TypeAlias = tuple[str, int, int]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _Entry:
    value: object
    expires_at: float


class Cache:
    def __init__(
        self, policy: CachePolicy | None = None, *, clock: Clock | None = None
    ) -> None:
        self._policy = policy if policy is not None else NoCache()
        self._clock = clock if clock is not None else SystemClock()
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._pending: dict[CacheKey, asyncio.Task[object]] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    @property
    def active_loaders(self) -> tuple[asyncio.Task[object], ...]:
        return tuple(self._tasks)

    def _entry(self, key: CacheKey) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is not None:
            if self._clock.monotonic() >= entry.expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
        return entry

    def peek(self, key: CacheKey) -> object | None:
        if self.is_loading(key):
            return None
        entry = self._entry(key)
        return None if entry is None else entry.value

    def is_loading(self, key: CacheKey) -> bool:
        task = self._pending.get(key)
        return task is not None and not task.done()

    async def get(
        self,
        key: CacheKey,
        load: Callable[[], Awaitable[_T]],
        *,
        refresh: bool = False,
        on_success: Callable[[], None] | None = None,
    ) -> _T:
        if self._closed:
            raise LifecycleError("resource is closed")
        task = self._pending.get(key)
        if task is None or task.done():
            if not refresh:
                entry = self._entry(key)
                if entry is not None:
                    # Each private key is always used with the same result type.
                    return cast(_T, entry.value)
            task = asyncio.create_task(self._load(key, load, on_success))
            self._pending[key] = task
            self._tasks.add(task)
            task.add_done_callback(lambda completed: self._done(key, completed))
        # One cancelled caller must not cancel a refresh shared by other callers.
        if not task.done():
            await asyncio.wait((task,))
        return cast(_T, task.result())

    async def _load(
        self,
        key: CacheKey,
        load: Callable[[], Awaitable[object]],
        on_success: Callable[[], None] | None,
    ) -> object:
        value = await load()
        if not self._closed and self._pending.get(key) is asyncio.current_task():
            if on_success is not None:
                on_success()
            if isinstance(self._policy, TTLCache):
                now = self._clock.monotonic()
                # Expired entries should not evict data that is still usable.
                expired = [
                    cache_key
                    for cache_key, entry in self._entries.items()
                    if entry.expires_at <= now
                ]
                for cache_key in expired:
                    del self._entries[cache_key]
                self._entries[key] = _Entry(
                    value, now + self._policy.ttl.total_seconds()
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._policy.max_entries:
                    self._entries.popitem(last=False)
        return value

    def _done(self, key: CacheKey, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if self._pending.get(key) is task:
            del self._pending[key]
        if not task.cancelled():
            # The last waiter may have been cancelled before the loader failed.
            task.exception()

    def invalidate(self, key: CacheKey) -> None:
        self._entries.pop(key, None)
        self._pending.pop(key, None)

    def discard_pages(self) -> None:
        for key in self._entries.keys() | self._pending.keys():
            if key[0] == "page":
                self.invalidate(key)

    def clear(self) -> None:
        self._entries.clear()
        # Detached loaders can finish for their callers but cannot repopulate the cache.
        self._pending.clear()

    async def close(self) -> None:
        self._closed = True
        self.clear()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
