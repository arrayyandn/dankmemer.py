from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar, cast

from dankmemer._clock import Clock
from dankmemer._parsing import Record
from dankmemer.config import CachePolicy
from dankmemer.errors import AmbiguousLookupError, ConfigurationError, PaginationError
from dankmemer.http._routes import Route
from dankmemer.pagination import Page

from ._base import PaginatedResource, Requester, validate_page
from ._queries import boolean, limit, normalise, rank, search_options, select, text


class Named(Protocol):
    @property
    def id(self) -> int | str | None: ...

    @property
    def name(self) -> str: ...


_T = TypeVar("_T", bound=Named)
_ID = TypeVar("_ID", int, str)
_CATALOG_KEY = ("catalog", 0, 0)


@dataclass(frozen=True, slots=True)
class _Catalog(Generic[_T]):
    items: tuple[_T, ...]
    ids: Mapping[int | str, _T]
    names: Mapping[str, tuple[_T, ...]]
    keys: Mapping[str, tuple[_T, ...]]


class CatalogResource(PaginatedResource[_T], Generic[_T, _ID]):
    def __init__(
        self,
        request: Requester,
        route: Route,
        parse: Callable[[Record], _T],
        id_type: type[_ID],
        *,
        identity: Callable[[_T], int | str] | None = None,
        key: Callable[[_T], str] | None = None,
        category: str | None = None,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            route,
            parse,
            identity=identity,
            category=category,
            cache=cache,
            clock=clock,
            guard=guard,
        )
        self._id_type: type[_ID] = id_type
        self._key = key

    async def fetch(
        self, *, page_size: int = 100, cursor: int = 0, refresh: bool = False
    ) -> Page[_T]:
        """Fetch a page, reusing a fresh complete catalog when available.

        ``page_size`` is from 1 to 100 and ``cursor`` is a nonnegative offset.
        ``refresh=True`` invalidates the complete catalog and requests a fresh
        page. The next lookup rebuilds the catalog before reporting a match.
        """
        validate_page(page_size, cursor)
        boolean(refresh, "refresh")
        self._check_access()
        if refresh:
            self._cache.invalidate(_CATALOG_KEY)
        else:
            snapshot = cast(_Catalog[_T] | None, self._cache.peek(_CATALOG_KEY))
            if self._cache.is_loading(_CATALOG_KEY):
                snapshot = await self._catalog()
            if snapshot is not None:
                items = snapshot.items[cursor : cursor + page_size]
                next_cursor = cursor + len(items)
                return Page(
                    items, next_cursor if next_cursor < len(snapshot.items) else None
                )
        return await super().fetch(page_size=page_size, cursor=cursor, refresh=refresh)

    async def _catalog(self, *, refresh: bool = False) -> _Catalog[_T]:
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(
            _CATALOG_KEY,
            self._load_catalog,
            refresh=refresh,
            on_success=self._cache.discard_pages,
        )

    async def _load_catalog(self) -> _Catalog[_T]:
        items: list[_T] = []
        ids: dict[int | str, _T] = {}
        names: dict[str, list[_T]] = {}
        keys: dict[str, list[_T]] = {}
        seen: set[int | str] = set()
        cursor = 0
        while True:
            # Every page belongs to this reload; cached pages can have different ages.
            page = await self._fetch_page(page_size=100, cursor=cursor)
            for item in page.items:
                identity = self._identity(item)
                if identity in seen:
                    raise PaginationError(
                        "catalog pages contain overlapping record IDs",
                        path=self._route.template,
                        field="data",
                    )
                seen.add(identity)
                if item.id is not None:
                    ids[item.id] = item
                items.append(item)
                names.setdefault(normalise(item.name), []).append(item)
                if self._key is not None:
                    keys.setdefault(self._key(item), []).append(item)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return _Catalog(
            tuple(items),
            MappingProxyType(ids),
            MappingProxyType({name: tuple(matches) for name, matches in names.items()}),
            MappingProxyType({key: tuple(matches) for key, matches in keys.items()}),
        )

    def _unique(self, matches: tuple[_T, ...], name: str) -> _T | None:
        if len(matches) > 1:
            raise AmbiguousLookupError(
                name=name,
                path=self._route.template,
                candidate_ids=tuple(item.id for item in matches if item.id is not None),
            )
        return matches[0] if matches else None

    async def get(self, name: str, *, refresh: bool = False) -> _T | None:
        """Look up an exact name, ignoring case and surrounding whitespace.

        Loads every catalog page when needed and reuses its TTL cache.
        Returns ``None`` when no record matches. Multiple matches raise
        :class:`~dankmemer.AmbiguousLookupError`; fuzzy matching is available
        explicitly through :meth:`search`.
        """
        name = text(name, "name")
        catalog = await self._catalog(refresh=refresh)
        return self._unique(catalog.names.get(normalise(name), ()), name)

    def _validate_id(self, record_id: object) -> None:
        if self._id_type is int:
            if type(record_id) is not int or record_id < 0:
                raise ConfigurationError("record_id must be a nonnegative integer")
        elif not isinstance(record_id, str) or not record_id:
            raise ConfigurationError("record_id must be a nonempty string")

    async def get_by_id(self, record_id: _ID, *, refresh: bool = False) -> _T | None:
        """Look up an exact API ID, returning ``None`` when it is absent.

        Items require integer IDs. Other catalogs require string IDs.
        Uses the same complete catalog and cache as name lookup.
        """
        self._validate_id(record_id)
        return (await self._catalog(refresh=refresh)).ids.get(record_id)

    async def get_many_by_id(
        self, *record_ids: _ID, refresh: bool = False
    ) -> tuple[_T | None, ...]:
        """Resolve IDs in one catalog load, preserving input order and duplicates.

        A missing ID produces ``None`` in its position. An empty input makes
        no requests. ``refresh=True`` reloads once for the whole operation.
        """
        boolean(refresh, "refresh")
        self._check_access()
        for record_id in record_ids:
            self._validate_id(record_id)
        if not record_ids:
            return ()
        catalog = await self._catalog(refresh=refresh)
        return tuple(catalog.ids.get(record_id) for record_id in record_ids)

    async def _get_by_key(self, key: str, *, refresh: bool) -> _T | None:
        key = text(key, "key")
        catalog = await self._catalog(refresh=refresh)
        return self._unique(catalog.keys.get(key, ()), key)

    async def search(
        self,
        query: str,
        *,
        fuzzy: bool = False,
        min_score: float = 70.0,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[_T, ...]:
        """Search catalog names, returning at most ``max_limit`` ranked records.

        The default search matches case-insensitive substrings, preferring
        exact names and prefixes. ``fuzzy=True`` uses RapidFuzz's weighted
        similarity and requires at least ``min_score`` on its 0–100 scale.
        Results are models ordered by relevance, with names breaking ties.
        Set ``max_limit=None`` for all matches, or 0 to make no requests.
        """
        query = search_options(query, fuzzy, min_score)
        limit(max_limit)
        boolean(refresh, "refresh")
        self._check_access()
        if max_limit == 0:
            return ()
        catalog = await self._catalog(refresh=refresh)
        return rank(
            catalog.items,
            query,
            lambda item: item.name,
            fuzzy=fuzzy,
            min_score=min_score,
            max_limit=max_limit,
        )

    async def _select(
        self, predicate: Callable[[_T], bool], *, max_limit: int | None, refresh: bool
    ) -> tuple[_T, ...]:
        limit(max_limit)
        boolean(refresh, "refresh")
        self._check_access()
        if max_limit == 0:
            return ()
        catalog = await self._catalog(refresh=refresh)
        return select(catalog.items, predicate, max_limit)

    async def _suggest(
        self, query: object, *, fuzzy: bool, fetch: bool
    ) -> tuple[_T, ...]:
        if not isinstance(query, str):
            raise ConfigurationError("query must be a string")
        normalized = normalise(query)
        search_options(normalized or "_", fuzzy, 70.0)
        boolean(fetch, "fetch")
        self._check_access()
        snapshot = (
            await self._catalog()
            if fetch
            else cast(_Catalog[_T] | None, self._cache.peek(_CATALOG_KEY))
        )
        if snapshot is None:
            return ()
        if not normalized:
            return tuple(sorted(snapshot.items, key=lambda item: normalise(item.name)))[
                :25
            ]
        return rank(
            snapshot.items,
            normalized,
            lambda item: item.name,
            fuzzy=fuzzy,
            min_score=70.0,
            max_limit=25,
        )
