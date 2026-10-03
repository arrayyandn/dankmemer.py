from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enum import Enum as _Enum
else:
    from ._enum import _Enum


class ClientState(_Enum):
    """The lifecycle state of a :class:`dankmemer.client.DankMemer` client."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"


class EventDelivery(_Enum):
    """Delivery modes for event listeners.

    ``BEST_EFFORT`` keeps pending callbacks in memory. ``DURABLE``
    replays unacknowledged callbacks after a restart, which can call a
    listener more than once. Configure this through :class:`dankmemer.EventConfig`.
    Best effort is the default. Durable delivery requires a persistent store
    and stable listener subscription IDs.
    """

    BEST_EFFORT = "best_effort"
    DURABLE = "durable"


class CoordinationMode(_Enum):
    """Choose how client processes own polling and callback delivery.

    With ``INDEPENDENT``, every process polls the resources needed by its own
    listeners and uses its own request budget. With ``SHARED``, clients use
    one persistent store and application ID to elect one poller per resource
    and share request allowances. Another client takes over expired ownership.
    Distinct subscription IDs each receive the event; the same ID on several
    clients shares one consumer. Shared mode requires durable delivery.
    Configure this through :class:`dankmemer.CoordinationConfig`.
    """

    INDEPENDENT = "independent"
    SHARED = "shared"


class PollingResource(_Enum):
    """Resources that may supply changes for automatic event observation."""

    DROPS = "drops"
    GLOBAL_BOOSTS = "global_boosts"
    STORE_SALES = "store_sales"
    STORE_DAILY_GIFTS = "store_daily_gifts"
    STREAM_TRENDING_GAME = "stream_trending_game"
    FISHING_EVENTS = "fishing_events"
    BLOGS = "blogs"
    CHANGELOGS = "changelogs"
    LOTTERY = "lottery"
    MERCHANT_TRADES = "merchant_trades"
