from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from dankmemer._clock import Clock
from dankmemer.config import CachePolicy
from dankmemer.errors import ConfigurationError
from dankmemer.http._routes import BLOGS, CHANGELOGS
from dankmemer.models.publications import Blog, Changelog, parse_blog, parse_changelog

from ._base import PaginatedResource, Requester
from ._queries import (
    aware,
    boolean,
    limit,
    normalise,
    optional_text,
    rank,
    search_options,
    select,
    text,
)


class _Publication(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...


_T = TypeVar("_T", bound=_Publication)


class _Publications(PaginatedResource[_T], Generic[_T]):
    async def latest(self, *, refresh: bool = False) -> _T | None:
        """Return the newest entry, or ``None`` for an empty collection."""
        page = await self.fetch(page_size=1, refresh=refresh)
        return page.items[0] if page.items else None

    async def _scan(self, scan_limit: int | None, *, refresh: bool) -> tuple[_T, ...]:
        limit(scan_limit, "scan_limit")
        boolean(refresh, "refresh")
        self._check_access()
        if scan_limit == 0:
            return ()
        page_size = 100 if scan_limit is None else min(100, scan_limit)
        return tuple(
            [
                entry
                async for entry in self.iter(
                    page_size=page_size, max_limit=scan_limit, refresh=refresh
                )
            ]
        )

    async def get_by_id(
        self,
        record_id: str,
        *,
        scan_limit: int | None = 100,
        refresh: bool = False,
    ) -> _T | None:
        """Find an ID among at most ``scan_limit`` entries, newest first.

        ``None`` means no match within that scan, not necessarily no match in
        the full publication history. Set ``scan_limit=None`` to explicitly
        traverse all available pages. An empty scan makes no requests.
        """
        record_id = text(record_id, "record_id")
        limit(scan_limit, "scan_limit")
        boolean(refresh, "refresh")
        self._check_access()
        if scan_limit == 0:
            return None
        page_size = 100 if scan_limit is None else min(100, scan_limit)
        async for entry in self.iter(
            page_size=page_size, max_limit=scan_limit, refresh=refresh
        ):
            if entry.id == record_id:
                return entry
        return None

    async def search(
        self,
        query: str,
        *,
        fuzzy: bool = False,
        min_score: float = 70.0,
        max_limit: int | None = 100,
        scan_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[_T, ...]:
        """Search titles within a bounded scan of the newest publications.

        ``scan_limit`` caps entries examined and defaults to 100. ``max_limit``
        separately caps ranked matches. Case-insensitive substring matching
        is the default; ``fuzzy=True`` uses a 0–100 similarity threshold.
        Passing ``scan_limit=None`` explicitly allows an unbounded traversal.
        """
        return await self._search(
            query,
            lambda entry: entry.title,
            fuzzy=fuzzy,
            min_score=min_score,
            max_limit=max_limit,
            scan_limit=scan_limit,
            refresh=refresh,
        )

    async def _search(
        self,
        query: str,
        searchable: Callable[[_T], str],
        *,
        fuzzy: bool,
        min_score: float,
        max_limit: int | None,
        scan_limit: int | None,
        refresh: bool,
    ) -> tuple[_T, ...]:
        query = search_options(query, fuzzy, min_score)
        limit(max_limit)
        limit(scan_limit, "scan_limit")
        boolean(refresh, "refresh")
        self._check_access()
        if max_limit == 0:
            return ()
        entries = await self._scan(scan_limit, refresh=refresh)
        return rank(
            entries,
            query,
            searchable,
            fuzzy=fuzzy,
            min_score=min_score,
            max_limit=max_limit,
        )

    async def filter(
        self,
        *,
        title: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        max_limit: int | None = 100,
        scan_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[_T, ...]:
        """Filter a bounded scan by exact title and inclusive publication dates.

        Date bounds must be timezone-aware. Matches retain API order, newest
        first. ``scan_limit`` limits entries examined independently of the
        number of matches. ``None`` explicitly removes either limit.
        """
        title = optional_text(title, "title")
        aware(after, "after")
        aware(before, "before")
        if after is not None and before is not None and after > before:
            raise ConfigurationError("after must not be later than before")
        limit(max_limit)
        limit(scan_limit, "scan_limit")
        boolean(refresh, "refresh")
        self._check_access()
        if max_limit == 0:
            return ()
        entries = await self._scan(scan_limit, refresh=refresh)
        return select(
            entries,
            lambda entry: (
                (title is None or normalise(entry.title) == title)
                and (after is None or entry.created_at >= after)
                and (before is None or entry.created_at <= before)
            ),
            max_limit,
        )


class Blogs(_Publications[Blog]):
    """Published blog summaries, ordered newest first by the API.

    Searches scan at most 100 entries by default. ``iter`` deduplicates IDs
    when new publications shift offsets; concurrent changes can still cause
    entries to be missed.
    """

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request, BLOGS, parse_blog, cache=cache, clock=clock, guard=guard
        )

    async def search(
        self,
        query: str,
        *,
        include_description: bool = False,
        fuzzy: bool = False,
        min_score: float = 70.0,
        max_limit: int | None = 100,
        scan_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[Blog, ...]:
        """Search titles and, optionally, descriptions within a bounded scan.

        ``scan_limit`` defaults to 100 entries examined; ``max_limit`` caps
        returned matches. Set ``include_description=True`` to search the
        combined title and summary. Fuzzy matching is explicit and uses the
        supplied ``min_score`` on a 0–100 scale. Full articles are not fetched.
        """
        boolean(include_description, "include_description")
        return await self._search(
            query,
            lambda entry: (
                f"{entry.title} {entry.description}"
                if include_description
                else entry.title
            ),
            fuzzy=fuzzy,
            min_score=min_score,
            max_limit=max_limit,
            scan_limit=scan_limit,
            refresh=refresh,
        )


class Changelogs(_Publications[Changelog]):
    """Published changelog summaries, ordered newest first by the API.

    Searches scan at most 100 entries by default. Offsets refer to the current
    collection; even deduplicated traversal can miss concurrent changes.
    """

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request, CHANGELOGS, parse_changelog, cache=cache, clock=clock, guard=guard
        )
