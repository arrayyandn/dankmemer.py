from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dankmemer._parsing import Record
from dankmemer.errors import ConfigurationError
from dankmemer.types import JSONValue

from .catalog import Item


@dataclass(frozen=True, slots=True)
class GlobalBoost:
    """An active global boost reported by the API.

    Attributes
    ----------
    type : str
        Boost type, such as coins or XP.
    multiplier : float
        Published multiplier, represented as a float.
    ends_at : datetime
        End of the active period, as an aware UTC datetime.
    """

    type: str
    multiplier: float
    ends_at: datetime

    @property
    def is_active(self) -> bool:
        """Whether the boost's end time is still in the future."""
        return datetime.now(UTC) < self.ends_at

    @property
    def time_remaining(self) -> timedelta:
        """Time until the boost ends, or zero if its end time has passed."""
        return max(timedelta(0), self.ends_at - datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class LotteryResult:
    """The latest completed lottery drawing.

    Attributes
    ----------
    drawn_at : datetime
        Time identifying this completed drawing, as an aware UTC datetime.
    winnings : int
        Published prize amount.
    total_entries : int
        Total number of entries in this drawing.
    participants : int
        Number of participating users.
    winner_entries : int
        Number of entries held by the winner. The winner's identity is not
        supplied.
    """

    drawn_at: datetime
    winnings: int
    total_entries: int
    participants: int
    winner_entries: int

    @property
    def age(self) -> timedelta:
        """Time since this result was drawn, clamped to zero for a future date."""
        return max(timedelta(0), datetime.now(UTC) - self.drawn_at)

    @property
    def winner_entry_fraction(self) -> float | None:
        """The winner's share of total entries, or ``None`` for a zero total.

        This describes the completed drawing, not a prediction for another.
        """
        return (
            None
            if self.total_entries <= 0
            else self.winner_entries / self.total_entries
        )

    @property
    def entries_per_participant(self) -> float | None:
        """Mean entries per participant, or ``None`` for zero participants."""
        return (
            None if self.participants <= 0 else self.total_entries / self.participants
        )


@dataclass(frozen=True, slots=True)
class Reward:
    """A resolved reward or cost reported by the API.

    Attributes
    ----------
    type : str
        Reward or cost type. Unknown types remain available through data.
    quantity : int | None
        Reported quantity, or None when omitted.
    item_id : int | None
        Integer item reference, or None for a reward without that field.
    bait_id : str | None
        Fishing bait reference, or None when omitted.
    boost : str | None
        Boost identifier, or None when omitted.
    multiplier : float | None
        Boost multiplier, or None when omitted.
    time : int | None
        Published numeric time value, or None when omitted. No duration unit is
        assumed.
    stack : str | None
        Stacking value as supplied by the API, or None when omitted.
    context : str | None
        Reward context as supplied by the API, or None when omitted.
    data : Mapping[str, JSONValue]
        Complete read-only reward record, including unfamiliar fields. Nested
        JSON arrays are tuples.
    """

    type: str
    quantity: int | None
    item_id: int | None
    bait_id: str | None
    boost: str | None
    multiplier: float | None
    time: int | None
    stack: str | None
    context: str | None
    data: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class MerchantTrade:
    """One offer in the merchant's daily rotation.

    Attributes
    ----------
    position : str
        Offer position identifier in the rotation.
    max : int
        Published limit for this offer.
    reward : Reward
        What the offer gives.
    cost : Reward
        What the offer requires; parsed from the API forReward field.
    """

    position: str
    max: int
    reward: Reward
    cost: Reward


@dataclass(frozen=True, slots=True)
class MerchantRotation:
    """The merchant's offers for one UTC day.

    Attributes
    ----------
    date : datetime
        UTC day identifying this rotation, as an aware UTC datetime.
    generated_at : datetime
        Time the rotation was generated, as an aware UTC datetime.
    trades : tuple[MerchantTrade, ...]
        Offers for this rotation.
    """

    date: datetime
    generated_at: datetime
    trades: tuple[MerchantTrade, ...]

    def trades_requiring(self, item: Item | int) -> tuple[MerchantTrade, ...]:
        """Select offers with this item cost, using already-loaded data.

        Accepts an item model or nonnegative integer ID. No requests are made.
        """
        item_id = _item_id(item.id if isinstance(item, Item) else item)
        return tuple(
            trade
            for trade in self.trades
            if trade.cost.type == "item" and trade.cost.item_id == item_id
        )

    def trades_rewarding(self, type: str) -> tuple[MerchantTrade, ...]:
        """Select offers with a reward type, ignoring case, without requests."""
        reward_type = _reward_type(type)
        return tuple(
            trade
            for trade in self.trades
            if trade.reward.type.casefold() == reward_type
        )


def _item_id(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ConfigurationError("item must be a model or nonnegative integer ID")
    return value


def _reward_type(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("type must be a nonempty string")
    return value.strip().casefold()


def parse_global_boost(record: Record) -> GlobalBoost:
    return GlobalBoost(
        type=record.string("type"),
        multiplier=record.number("multiplier"),
        ends_at=record.datetime("endsAt"),
    )


def parse_lottery(record: Record) -> LotteryResult:
    return LotteryResult(
        drawn_at=record.datetime("drawnAt"),
        winnings=record.integer("winnings"),
        total_entries=record.integer("totalEntries"),
        participants=record.integer("participants"),
        winner_entries=record.integer("winnerEntries"),
    )


def parse_reward(record: Record) -> Reward:
    return Reward(
        type=record.string("type"),
        quantity=record.optional_integer("quantity"),
        item_id=record.optional_integer("item"),
        bait_id=record.optional_string("baitID"),
        boost=record.optional_string("boost"),
        multiplier=record.optional_number("multiplier"),
        time=record.optional_integer("time"),
        stack=record.optional_string("stack"),
        context=record.optional_string("context"),
        data=record.json_object(),
    )


def parse_merchant_rotation(record: Record) -> MerchantRotation:
    return MerchantRotation(
        date=record.datetime("date"),
        generated_at=record.datetime("generatedAt"),
        trades=tuple(
            MerchantTrade(
                position=trade.string("position"),
                max=trade.integer("max"),
                reward=parse_reward(trade.record("reward")),
                cost=parse_reward(trade.record("forReward")),
            )
            for trade in record.records("trades")
        ),
    )
