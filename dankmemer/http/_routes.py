from __future__ import annotations

from dataclasses import dataclass

from dankmemer.enums import PollingResource
from dankmemer.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Route:
    """A registered API path and the query options it accepts."""

    template: str
    event_resource: PollingResource | None = None
    paginated: bool = False
    fishing: bool = False

    def target(
        self,
        *,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> tuple[str, dict[str, int | str]]:
        """Build a validated path and query from request arguments."""
        if self is USER_BAN_STATUS:
            if type(user_id) is not int or user_id <= 0:
                raise ConfigurationError("user_id must be a positive integer")
            path = self.template.replace("{user_id}", str(user_id))
        elif user_id is None:
            path = self.template
        else:
            raise ConfigurationError("user_id is unsupported for this route")

        query: dict[str, int | str] = {}
        if cursor is not None or limit is not None:
            if not self.paginated:
                raise ConfigurationError("pagination is unsupported for this route")
            if cursor is not None:
                if type(cursor) is not int or cursor < 0:
                    raise ConfigurationError("cursor must be a nonnegative integer")
                query["cursor"] = cursor
            if limit is not None:
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise ConfigurationError("limit must be an integer from 1 to 100")
                query["limit"] = limit

        if self.fishing:
            if category not in _FISHING_CATEGORIES:
                raise ConfigurationError("unknown fishing category")
            assert category is not None
            query["category"] = category
        elif category is not None:
            raise ConfigurationError("category is unsupported for this route")
        return path, query


_FISHING_CATEGORIES = frozenset(
    {"creatures", "locations", "npcs", "tools", "baits", "buckets", "skills"}
)

ITEMS = Route("/items", paginated=True)
PETS = Route("/pets", paginated=True)
SKINS = Route("/skins", paginated=True)
COMMANDS = Route("/commands", paginated=True)
FISH = Route("/fish", paginated=True, fishing=True)
USER_BAN_STATUS = Route("/users/{user_id}/ban-status")
GLOBAL_BOOSTS = Route("/global-boosts", PollingResource.GLOBAL_BOOSTS)
BLOGS = Route("/blogs", PollingResource.BLOGS, paginated=True)
CHANGELOGS = Route("/changelogs", PollingResource.CHANGELOGS, paginated=True)
STORE_SALES = Route("/store-sales", PollingResource.STORE_SALES)
STORE_DAILY_GIFTS = Route("/store-daily-gifts", PollingResource.STORE_DAILY_GIFTS)
STREAM_TRENDING_GAME = Route(
    "/stream-trending-game", PollingResource.STREAM_TRENDING_GAME
)
LOTTERY = Route("/lottery", PollingResource.LOTTERY)
MERCHANT_TRADES = Route("/merchant-trades", PollingResource.MERCHANT_TRADES)
DROPS = Route("/drops", PollingResource.DROPS)
FISHING_EVENTS = Route("/fishing-events", PollingResource.FISHING_EVENTS)

_ALL_ROUTES = (
    ITEMS,
    PETS,
    SKINS,
    COMMANDS,
    FISH,
    USER_BAN_STATUS,
    GLOBAL_BOOSTS,
    BLOGS,
    CHANGELOGS,
    STORE_SALES,
    STORE_DAILY_GIFTS,
    STREAM_TRENDING_GAME,
    LOTTERY,
    MERCHANT_TRADES,
    DROPS,
    FISHING_EVENTS,
)


def check_route(route: Route) -> None:
    """Reject a route object outside the SDK's fixed registry."""
    if not any(route is registered for registered in _ALL_ROUTES):
        raise ConfigurationError("route is not in the verified API registry")
