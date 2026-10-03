from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from dankmemer._parsing import Record
from dankmemer.types import JSONValue

from .activities import Reward, parse_reward


class _ActivePeriod:
    __slots__ = ()
    starts_at: datetime
    ends_at: datetime

    @property
    def is_active(self) -> bool:
        """Whether the current UTC time is inside this record's active period."""
        return self.starts_at <= datetime.now(UTC) < self.ends_at

    @property
    def time_remaining(self) -> timedelta:
        """Time until expiry, clamped to zero after the end time."""
        return max(timedelta(0), self.ends_at - datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Drop(_ActivePeriod):
    """A drop reported as active by the API.

    Attributes
    ----------
    id : int
        Integer drop ID. It identifies the drop rather than its reward.
    kind : str | None
        Drop kind, or None when the API supplies no kind.
    reward : Reward | None
        Resolved reward, or None when no reward is supplied.
    cost : Reward | None
        Resolved cost, or None when no cost is supplied.
    special_cost : float | None
        Special cost number, or None when absent. No currency conversion is
        applied.
    starts_at : datetime
        Beginning of the active period, as an aware UTC datetime.
    ends_at : datetime
        End of the active period, as an aware UTC datetime.
    total_stock : int
        Configured starting stock; this is not remaining stock.
    limit_per_user : int
        Published per-user limit.
    patreon_only : bool
        Whether the drop is restricted to Patreon members.
    partner_only : bool
        Whether the drop is restricted to partners.
    """

    id: int
    kind: str | None
    reward: Reward | None
    cost: Reward | None
    special_cost: float | None
    starts_at: datetime
    ends_at: datetime
    total_stock: int
    limit_per_user: int
    patreon_only: bool
    partner_only: bool


@dataclass(frozen=True, slots=True)
class StoreSale(_ActivePeriod):
    """An active store discount for an item or subscription.

    Attributes
    ----------
    id : int
        Integer sale ID, distinct from the discounted subject ID.
    subject_id : int
        Integer identifier of the discounted subject.
    subject_kind : str
        Kind of discounted subject, such as an item or subscription.
    starts_at : datetime
        Beginning of the active period, as an aware UTC datetime.
    ends_at : datetime
        End of the active period, as an aware UTC datetime.
    price : float
        Published price, represented as a float without currency conversion.
    name : str
        Published name for the discounted subject.
    url : str
        Link supplied for this sale.
    """

    id: int
    subject_id: int
    subject_kind: str
    starts_at: datetime
    ends_at: datetime
    price: float
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class StoreGiftMetadata:
    """Display text and image for a resolved daily gift.

    Attributes
    ----------
    render : str
        Display text for the resolved reward.
    image_url : str
        URL of the record's image.
    data : Mapping[str, JSONValue]
        Complete read-only metadata, including additional rendering fields.
    """

    render: str
    image_url: str
    data: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class StoreDailyGift:
    """One free store reward pool for a UTC day.

    Attributes
    ----------
    id : str
        Gift pool ID. It can recur on later days; combine it with day when
        storing a gift identity.
    name : str
        Display name.
    category : str
        Category identifier supplied by the API.
    day : datetime
        UTC day of this gift, as an aware UTC datetime.
    expires_at : datetime
        Expiry time, as an aware UTC datetime.
    reward : Reward
        Resolved reward for this gift.
    metadata : StoreGiftMetadata
        Display text, image, and additional reward metadata.
    """

    id: str
    name: str
    category: str
    day: datetime
    expires_at: datetime
    reward: Reward
    metadata: StoreGiftMetadata

    @property
    def is_active(self) -> bool:
        """Whether this pool's reported day has begun and it has not expired."""
        return self.day <= datetime.now(UTC) < self.expires_at

    @property
    def time_remaining(self) -> timedelta:
        """Time until the gift expires, clamped to zero after expiry."""
        return max(timedelta(0), self.expires_at - datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FishingEvent(_ActivePeriod):
    """An active fishing event, distinct from the fishing reference catalog.

    Attributes
    ----------
    id : str
        Identifier of this active event occurrence.
    type_id : str
        Identifier of the fishing event type.
    name : str
        Display name.
    description : str
        Published description.
    image_url : str
        URL of the record's image.
    premium_only : bool
        Whether this event is restricted to premium users.
    starts_at : datetime
        Beginning of the active period, as an aware UTC datetime.
    ends_at : datetime
        End of the active period, as an aware UTC datetime.
    """

    id: str
    type_id: str
    name: str
    description: str
    image_url: str
    premium_only: bool
    starts_at: datetime
    ends_at: datetime


class _Identified(Protocol):
    @property
    def id(self) -> int | str: ...


_T = TypeVar("_T", bound=_Identified)


def parse_live_collection(
    record: Record, parse: Callable[[Record], _T]
) -> tuple[_T, ...]:
    values = tuple(parse(entry) for entry in record.records("data"))
    if len({value.id for value in values}) != len(values):
        raise record.error("collection contains duplicate record IDs", "data")
    return values


def parse_drop(record: Record) -> Drop:
    reward = record.nullable_record("reward")
    cost = record.nullable_record("cost")
    return Drop(
        id=record.integer("id"),
        kind=record.nullable_string("kind"),
        reward=None if reward is None else parse_reward(reward),
        cost=None if cost is None else parse_reward(cost),
        special_cost=record.nullable_number("specialCost"),
        starts_at=record.datetime("startsAt"),
        ends_at=record.datetime("endsAt"),
        total_stock=record.integer("totalStock"),
        limit_per_user=record.integer("limitPerUser"),
        patreon_only=record.boolean("patreonOnly"),
        partner_only=record.boolean("partnerOnly"),
    )


def parse_store_sale(record: Record) -> StoreSale:
    return StoreSale(
        id=record.integer("id"),
        subject_id=record.integer("subjectID"),
        subject_kind=record.string("subjectKind"),
        starts_at=record.datetime("startsAt"),
        ends_at=record.datetime("endsAt"),
        price=record.number("price"),
        name=record.string("name"),
        url=record.string("url"),
    )


def parse_store_daily_gift(record: Record) -> StoreDailyGift:
    metadata = record.record("metadata")
    return StoreDailyGift(
        id=record.string("id"),
        name=record.string("name"),
        category=record.string("category"),
        day=record.datetime("day"),
        expires_at=record.datetime("expiresAt"),
        reward=parse_reward(record.record("reward")),
        metadata=StoreGiftMetadata(
            render=metadata.string("render"),
            image_url=metadata.string("imageUrl"),
            data=metadata.json_object(),
        ),
    )


def parse_fishing_event(record: Record) -> FishingEvent:
    return FishingEvent(
        id=record.string("id"),
        type_id=record.string("typeId"),
        name=record.string("name"),
        description=record.string("description"),
        image_url=record.string("imageUrl"),
        premium_only=record.boolean("premiumOnly"),
        starts_at=record.datetime("startsAt"),
        ends_at=record.datetime("endsAt"),
    )
