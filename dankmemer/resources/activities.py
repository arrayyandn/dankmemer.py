from __future__ import annotations

from collections.abc import Callable

from dankmemer._clock import Clock
from dankmemer.config import CachePolicy
from dankmemer.errors import DankMemerResponseError
from dankmemer.http._routes import (
    GLOBAL_BOOSTS,
    LOTTERY,
    MERCHANT_TRADES,
    STREAM_TRENDING_GAME,
)
from dankmemer.models.activities import (
    GlobalBoost,
    LotteryResult,
    MerchantRotation,
    MerchantTrade,
    parse_global_boost,
    parse_lottery,
    parse_merchant_rotation,
)
from dankmemer.models.catalog import Item
from dankmemer.models.fishing import FishingBait

from ._base import Requester, Resource
from ._queries import boolean, model, normalise, optional_text
from .catalog import Items
from .fishing import Baits


class GlobalBoosts(Resource):
    """Access the global boosts that the API currently reports as active."""

    async def active(
        self, *, type: str | None = None, refresh: bool = False
    ) -> tuple[GlobalBoost, ...]:
        """Return active boosts, or an empty tuple when there are none.

        The API does not provide past boosts. Its live cache can briefly
        retain a boost whose end time has passed.
        ``type`` optionally filters the boost type, ignoring case.
        ``refresh=True`` bypasses an opted-in SDK cache.
        """
        boost_type = optional_text(type, "type")
        boolean(refresh, "refresh")
        self._check_access()
        boosts = await self._cache.get(("value", 0, 0), self._load, refresh=refresh)
        return tuple(
            boost
            for boost in boosts
            if boost_type is None or normalise(boost.type) == boost_type
        )

    async def _load(self) -> tuple[GlobalBoost, ...]:
        response = await self._get(GLOBAL_BOOSTS)
        return tuple(parse_global_boost(record) for record in response.records("data"))


class Lottery(Resource):
    """Access the latest completed lottery drawing."""

    async def latest(self, *, refresh: bool = False) -> LotteryResult | None:
        """Return the latest completed drawing, or ``None`` for explicit null data.

        A new drawing replaces the previous result. The API provides no
        drawing history, and the next result can arrive after the hour.
        HTTP errors, including 404, are raised rather than treated as absence.
        ``refresh=True`` bypasses an opted-in SDK cache.
        """
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(("value", 0, 0), self._load, refresh=refresh)

    async def _load(self) -> LotteryResult | None:
        response = await self._get(LOTTERY)
        record = response.nullable_record("data")
        return None if record is None else parse_lottery(record)


class MerchantTrades(Resource):
    """Access the merchant's current UTC-day rotation."""

    def __init__(
        self,
        request: Requester,
        *,
        items: Items,
        baits: Baits,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(request, cache=cache, clock=clock, guard=guard)
        self._items = items
        self._baits = baits

    async def today(self, *, refresh: bool = False) -> MerchantRotation | None:
        """Return today's rotation, or ``None`` for explicit null data.

        Earlier rotations cannot be requested through this resource.
        HTTP errors, including 404, are raised rather than treated as absence.
        ``refresh=True`` bypasses an opted-in SDK cache.
        """
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(("value", 0, 0), self._load, refresh=refresh)

    async def _load(self) -> MerchantRotation | None:
        response = await self._get(MERCHANT_TRADES)
        record = response.nullable_record("data")
        return None if record is None else parse_merchant_rotation(record)

    async def for_item(
        self, item: Item | str, *, refresh: bool = False
    ) -> tuple[MerchantTrade, ...]:
        """Return today's offers requiring an item model or exact item name.

        Name lookup shares the client's item catalog cache. Unknown names or
        absent rotation data produce an empty tuple. HTTP errors are raised.
        """
        boolean(refresh, "refresh")
        self._check_access()
        if isinstance(item, str):
            found = await self._items.get(item, refresh=refresh)
            if found is None:
                return ()
            item = found
        item = model(item, Item, "item")
        rotation = await self.today(refresh=refresh)
        return () if rotation is None else rotation.trades_requiring(item)

    async def resolve_cost(
        self, trade: MerchantTrade, *, refresh: bool = False
    ) -> Item | None:
        """Resolve an item cost through the shared item catalog.

        Other cost types return ``None``. An item reference missing from the
        cost or catalog raises a response error.
        """
        trade = model(trade, MerchantTrade, "trade")
        boolean(refresh, "refresh")
        self._check_access()
        if trade.cost.type != "item":
            return None
        if trade.cost.item_id is not None:
            result = await self._items.get_by_id(trade.cost.item_id, refresh=refresh)
            if result is not None:
                return result
        raise DankMemerResponseError(
            "trade cost item is absent from the catalog or offer",
            path=MERCHANT_TRADES.template,
            field="forReward.item",
        )

    async def resolve_reward(
        self, trade: MerchantTrade, *, refresh: bool = False
    ) -> Item | FishingBait | None:
        """Resolve item or fish-bait rewards using their shared catalog caches.

        Other reward types return ``None``. A supported reward's missing
        reference raises a response error; no request is made for other types.
        """
        trade = model(trade, MerchantTrade, "trade")
        boolean(refresh, "refresh")
        self._check_access()
        reward = trade.reward
        if reward.type == "item":
            if reward.item_id is not None:
                item = await self._items.get_by_id(reward.item_id, refresh=refresh)
                if item is not None:
                    return item
            field = "reward.item"
        elif reward.type == "fish-bait":
            if reward.bait_id is not None:
                bait = await self._baits.get_by_id(reward.bait_id, refresh=refresh)
                if bait is not None:
                    return bait
            field = "reward.baitID"
        else:
            return None
        raise DankMemerResponseError(
            "trade reward is absent from the catalog or offer",
            path=MERCHANT_TRADES.template,
            field=field,
        )


class Stream(Resource):
    """Access the official streaming resource."""

    async def trending_game(self, *, refresh: bool = False) -> str:
        """Return the trending game's name for the current UTC day.

        Uses the documented ``/stream-trending-game`` route. If that route
        is unavailable, :class:`~dankmemer.NotFound` is raised.
        ``refresh=True`` bypasses an opted-in SDK cache.
        """
        boolean(refresh, "refresh")
        self._check_access()
        return await self._cache.get(("value", 0, 0), self._load, refresh=refresh)

    async def _load(self) -> str:
        response = await self._get(STREAM_TRENDING_GAME)
        return response.record("data").string("game")
