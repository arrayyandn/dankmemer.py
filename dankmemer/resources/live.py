from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from dankmemer._clock import Clock
from dankmemer._parsing import Record
from dankmemer.config import CachePolicy
from dankmemer.errors import DankMemerResponseError
from dankmemer.http._routes import (
    DROPS,
    FISHING_EVENTS,
    STORE_DAILY_GIFTS,
    STORE_SALES,
    Route,
)
from dankmemer.models.catalog import Item
from dankmemer.models.live import (
    Drop,
    FishingEvent,
    StoreDailyGift,
    StoreSale,
    parse_drop,
    parse_fishing_event,
    parse_live_collection,
    parse_store_daily_gift,
    parse_store_sale,
)

from ._base import Requester, Resource
from ._queries import (
    boolean,
    integer,
    model,
    normalise,
    optional_boolean,
    optional_text,
    text,
)
from .catalog import Items

_T = TypeVar("_T", Drop, StoreSale, StoreDailyGift, FishingEvent)


class _LiveCollection(Resource, Generic[_T]):
    def __init__(
        self,
        request: Requester,
        route: Route,
        parse: Callable[[Record], _T],
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(request, cache=cache, clock=clock, guard=guard)
        self._route = route
        self._parse: Callable[[Record], _T] = parse

    async def _values(self, *, refresh: bool) -> tuple[_T, ...]:
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(("value", 0, 0), self._load, refresh=refresh)

    async def _load(self) -> tuple[_T, ...]:
        return parse_live_collection(await self._get(self._route), self._parse)


class Drops(_LiveCollection[Drop]):
    """Look up the drops currently reported as active."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request, DROPS, parse_drop, cache=cache, clock=clock, guard=guard
        )

    async def active(
        self,
        *,
        kind: str | None = None,
        patreon_only: bool | None = None,
        partner_only: bool | None = None,
        refresh: bool = False,
    ) -> tuple[Drop, ...]:
        """Return active drops matching every supplied filter.

        kind matches case-insensitively. No filters are sent to the API.
        An empty collection returns an empty tuple. refresh bypasses the SDK cache.
        """
        kind = optional_text(kind, "kind")
        optional_boolean(patreon_only, "patreon_only")
        optional_boolean(partner_only, "partner_only")
        return tuple(
            drop
            for drop in await self._values(refresh=refresh)
            if (
                kind is None or (drop.kind is not None and normalise(drop.kind) == kind)
            )
            and (patreon_only is None or drop.patreon_only is patreon_only)
            and (partner_only is None or drop.partner_only is partner_only)
        )

    async def get(self, drop_id: int, *, refresh: bool = False) -> Drop | None:
        """Find an integer drop ID in the current collection, or return None."""
        drop_id = integer(drop_id, "drop_id")
        return next(
            (
                drop
                for drop in await self._values(refresh=refresh)
                if drop.id == drop_id
            ),
            None,
        )


class StoreSales(_LiveCollection[StoreSale]):
    """Look up published store discounts active now."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            STORE_SALES,
            parse_store_sale,
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def active(
        self,
        *,
        subject_kind: str | None = None,
        refresh: bool = False,
    ) -> tuple[StoreSale, ...]:
        """Return active sales, optionally selecting the subject kind.

        The filter ignores case and is applied locally. Empty collections
        return an empty tuple; HTTP errors propagate.
        """
        subject_kind = optional_text(subject_kind, "subject_kind")
        return tuple(
            sale
            for sale in await self._values(refresh=refresh)
            if subject_kind is None or normalise(sale.subject_kind) == subject_kind
        )

    async def get(self, sale_id: int, *, refresh: bool = False) -> StoreSale | None:
        """Find a sale ID in the current collection, or return None."""
        sale_id = integer(sale_id, "sale_id")
        return next(
            (
                sale
                for sale in await self._values(refresh=refresh)
                if sale.id == sale_id
            ),
            None,
        )

    async def for_item(
        self, item: Item | int, *, refresh: bool = False
    ) -> tuple[StoreSale, ...]:
        """Return active item sales for an item model or nonnegative item ID."""
        item_id = integer(item.id if isinstance(item, Item) else item, "item")
        return tuple(
            sale
            for sale in await self._values(refresh=refresh)
            if sale.subject_kind == "item" and sale.subject_id == item_id
        )


class StoreDailyGifts(_LiveCollection[StoreDailyGift]):
    """Access today's free gift pools without user claim information."""

    def __init__(
        self,
        request: Requester,
        *,
        items: Items,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            STORE_DAILY_GIFTS,
            parse_store_daily_gift,
            cache=cache,
            clock=clock,
            guard=guard,
        )
        self._items = items

    async def today(
        self,
        *,
        category: str | None = None,
        refresh: bool = False,
    ) -> tuple[StoreDailyGift, ...]:
        """Return today's pools, optionally filtering their category locally.

        Category matching ignores case. An empty response returns an empty
        tuple. Pools do not contain claim status or future rewards.
        """
        category = optional_text(category, "category")
        return tuple(
            gift
            for gift in await self._values(refresh=refresh)
            if category is None or normalise(gift.category) == category
        )

    async def get(
        self, pool_id: str, *, refresh: bool = False
    ) -> StoreDailyGift | None:
        """Find an exact pool ID in today's collection, or return None."""
        pool_id = text(pool_id, "pool_id")
        return next(
            (
                gift
                for gift in await self._values(refresh=refresh)
                if gift.id == pool_id
            ),
            None,
        )

    async def reward_item(
        self, gift: StoreDailyGift, *, refresh: bool = False
    ) -> Item | None:
        """Resolve an item reward through the cached item catalog.

        Returns None for other reward types. A missing item ID or absent
        catalog item raises DankMemerResponseError instead of hiding bad data.
        """
        gift = model(gift, StoreDailyGift, "gift")
        boolean(refresh, "refresh")
        self._check_access()
        if gift.reward.type != "item":
            return None
        if gift.reward.item_id is not None:
            item = await self._items.get_by_id(gift.reward.item_id, refresh=refresh)
            if item is not None:
                return item
        raise DankMemerResponseError(
            "gift reward is absent from the catalog or pool",
            path=STORE_DAILY_GIFTS.template,
            field="reward.item",
        )


class FishingEvents(_LiveCollection[FishingEvent]):
    """Access active fishing events and filter their published flags."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISHING_EVENTS,
            parse_fishing_event,
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def active(
        self,
        *,
        type_id: str | None = None,
        premium_only: bool | None = None,
        refresh: bool = False,
    ) -> tuple[FishingEvent, ...]:
        """Return active events matching an exact type ID and premium flag.

        Filters are applied locally. An empty collection returns an empty
        tuple. refresh bypasses the SDK cache.
        """
        type_id = None if type_id is None else text(type_id, "type_id")
        optional_boolean(premium_only, "premium_only")
        return tuple(
            event
            for event in await self._values(refresh=refresh)
            if (type_id is None or event.type_id == type_id)
            and (premium_only is None or event.premium_only is premium_only)
        )

    async def get(self, event_id: str, *, refresh: bool = False) -> FishingEvent | None:
        """Find an exact event ID in the current collection, or return None."""
        event_id = text(event_id, "event_id")
        return next(
            (
                event
                for event in await self._values(refresh=refresh)
                if event.id == event_id
            ),
            None,
        )
