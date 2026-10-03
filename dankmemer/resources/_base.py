from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Generic, Protocol, TypeVar

from dankmemer._cache import Cache
from dankmemer._clock import Clock
from dankmemer._parsing import Record
from dankmemer.config import CachePolicy
from dankmemer.errors import ConfigurationError, PaginationError
from dankmemer.http._routes import Route
from dankmemer.pagination import Page

from ._queries import boolean


class Requester(Protocol):
    def __call__(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> Awaitable[object]: ...


class Identified(Protocol):
    @property
    def id(self) -> int | str | None: ...


_T = TypeVar("_T", bound=Identified)


class Resource:
    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        self._request = request
        self._cache = Cache(cache, clock=clock)
        self._guard = guard

    def _check_access(self) -> None:
        if self._guard is not None:
            self._guard()

    def clear_cache(self) -> None:
        """Discard this resource's cached data.

        The next request reloads data when needed. An older request already
        in progress can finish for its callers, but cannot refill the cache.
        """
        self._cache.clear()

    async def _close(self) -> None:
        await self._cache.close()

    @property
    def _active_loaders(self) -> tuple[asyncio.Task[object], ...]:
        return self._cache.active_loaders

    async def _get(
        self,
        route: Route,
        *,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> Record:
        self._check_access()
        payload = await self._request(
            route, user_id=user_id, cursor=cursor, limit=limit, category=category
        )
        return Record(payload, path=route.template, field="")


class PaginatedResource(Resource, Generic[_T]):
    def __init__(
        self,
        request: Requester,
        route: Route,
        parse: Callable[[Record], _T],
        *,
        identity: Callable[[_T], int | str] | None = None,
        category: str | None = None,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(request, cache=cache, clock=clock, guard=guard)
        self._route = route
        self._parse = parse
        self._category = category
        self._identity_override = identity

    def _identity(self, item: _T) -> int | str:
        if self._identity_override is not None:
            return self._identity_override(item)
        if item.id is None:
            raise PaginationError("record has no identity", path=self._route.template)
        return item.id

    async def fetch(
        self, *, page_size: int = 100, cursor: int = 0, refresh: bool = False
    ) -> Page[_T]:
        """Fetch one page of records, reusing the configured cache when possible.

        Parameters
        ----------
        page_size: int
            Number of records to request, from 1 to 100. Defaults to 100.
        cursor: int
            Nonnegative offset into the current collection. Defaults to 0.
            Use a previous page's ``next_cursor`` to request another page.
        refresh: bool
            Bypass any cached page. Defaults to ``False``.

        Returns
        -------
        Page
            Records in API order and the next offset, if there is one.

        Raises
        ------
        ConfigurationError
            A pagination argument is invalid.
        DankMemerResponseError
            A record does not match the expected response format.
        PaginationError
            The page exceeds the requested size, repeats an ID, or supplies
            an offset that does not advance.
        """
        validate_page(page_size, cursor)
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(
            ("page", cursor, page_size),
            lambda: self._fetch_page(page_size=page_size, cursor=cursor),
            refresh=refresh,
        )

    async def _fetch_page(self, *, page_size: int, cursor: int) -> Page[_T]:
        response = await self._get(
            self._route, limit=page_size, cursor=cursor, category=self._category
        )
        if self._category is not None and response.string("category") != self._category:
            raise response.error(
                "response contains a different fishing category", "category"
            )
        records = response.records("data")
        next_cursor = response.value("nextCursor")
        if next_cursor is not None and (
            type(next_cursor) is not int or next_cursor <= cursor
        ):
            raise PaginationError(
                "next cursor must advance the current offset",
                path=self._route.template,
                field="nextCursor",
            )
        if len(records) > page_size:
            raise PaginationError(
                "page exceeds the requested size",
                path=self._route.template,
                field="data",
            )
        if not records and next_cursor is not None:
            raise PaginationError(
                "empty page supplies a next cursor",
                path=self._route.template,
                field="nextCursor",
            )
        items = tuple(self._parse(record) for record in records)
        if len({self._identity(item) for item in items}) != len(items):
            raise PaginationError(
                "page contains duplicate record IDs",
                path=self._route.template,
                field="data",
            )
        return Page(items=items, next_cursor=next_cursor)

    def iter(
        self,
        *,
        page_size: int = 100,
        cursor: int = 0,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> AsyncIterator[_T]:
        """Iterate over records, fetching another page only when needed.

        ``page_size`` is the per-request limit from 1 to 100. ``cursor`` is
        the initial offset. ``max_limit`` limits the number of unique records
        yielded and defaults to 100. Set it to ``None`` to explicitly request
        every available page, or 0 to make no requests.

        Fresh cached data is reused. ``refresh=True`` requests fresh pages;
        on reference catalogs it also invalidates the complete catalog cache.

        Overlapping pages are deduplicated by ID (command names for commands).
        Offsets refer to the current
        collection, so entries added or removed during traversal can still be
        missed. This iterator does not provide a historical snapshot.

        Invalid arguments raise :class:`~dankmemer.ConfigurationError` when
        this method is called. Response and HTTP errors occur during iteration.
        A page that repeats only previously yielded IDs while supplying another
        cursor raises :class:`~dankmemer.PaginationError` to stop traversal.
        """
        validate_page(page_size, cursor)
        boolean(refresh, "refresh")
        if max_limit is not None and (type(max_limit) is not int or max_limit < 0):
            raise ConfigurationError("max_limit must be a nonnegative integer or None")
        return self._iterate(
            page_size=page_size, cursor=cursor, max_limit=max_limit, refresh=refresh
        )

    async def _iterate(
        self, *, page_size: int, cursor: int, max_limit: int | None, refresh: bool
    ) -> AsyncIterator[_T]:
        seen: set[int | str] = set()
        self._check_access()
        yielded = 0
        while max_limit is None or yielded < max_limit:
            limit = (
                page_size if max_limit is None else min(page_size, max_limit - yielded)
            )
            page = await self.fetch(page_size=limit, cursor=cursor, refresh=refresh)
            previous_count = yielded
            for item in page.items:
                identity = self._identity(item)
                if identity in seen:
                    continue
                seen.add(identity)
                yielded += 1
                yield item
                if max_limit is not None and yielded >= max_limit:
                    return
            if page.next_cursor is None:
                return
            # Advancing offsets alone cannot prove the collection is progressing.
            if yielded == previous_count:
                raise PaginationError(
                    "page contains no new record IDs",
                    path=self._route.template,
                    field="data",
                )
            cursor = page.next_cursor


def validate_page(page_size: int, cursor: int) -> None:
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ConfigurationError("page_size must be an integer from 1 to 100")
    if type(cursor) is not int or cursor < 0:
        raise ConfigurationError("cursor must be a nonnegative integer")
