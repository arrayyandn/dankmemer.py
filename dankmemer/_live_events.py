from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar

from ._observations import Emission
from .models.live import Drop, FishingEvent, StoreDailyGift, StoreSale


class _Identified(Protocol):
    @property
    def id(self) -> int | str: ...


_T = TypeVar("_T", bound=_Identified)


def _collection_changes(
    event: str,
    prefix: str,
    previous: tuple[_T, ...] | None,
    current: tuple[_T, ...],
) -> tuple[Emission, ...]:
    before = () if previous is None else previous
    if before == current:
        return ()
    old = {entry.id: entry for entry in before}
    new = {entry.id: entry for entry in current}
    emissions = [Emission(event, (before, current))]
    for entry in current:
        if entry.id not in old:
            emissions.append(Emission(prefix + "_started", (entry,)))
        elif entry != old[entry.id]:
            emissions.append(Emission(prefix + "_updated", (old[entry.id], entry)))
    emissions.extend(
        Emission(prefix + "_ended", (entry,)) for entry in before if entry.id not in new
    )
    return tuple(emissions)


def drop_changes(
    previous: tuple[Drop, ...] | None,
    current: tuple[Drop, ...],
    initial: bool,
) -> tuple[Emission, ...]:
    return _collection_changes("drops_changed", "drop", previous, current)


def sale_changes(
    previous: tuple[StoreSale, ...] | None,
    current: tuple[StoreSale, ...],
    initial: bool,
) -> tuple[Emission, ...]:
    return _collection_changes("store_sales_changed", "store_sale", previous, current)


def fishing_event_changes(
    previous: tuple[FishingEvent, ...] | None,
    current: tuple[FishingEvent, ...],
    initial: bool,
) -> tuple[Emission, ...]:
    return _collection_changes(
        "fishing_events_changed", "fishing_event", previous, current
    )


def gifts_allowed(
    previous: tuple[StoreDailyGift, ...],
    current: tuple[StoreDailyGift, ...],
) -> bool:
    return (
        not previous
        or not current
        or (
            max(gift.day.date() for gift in current)
            >= max(gift.day.date() for gift in previous)
        )
    )


def gift_changes(
    previous: tuple[StoreDailyGift, ...] | None,
    current: tuple[StoreDailyGift, ...],
    initial: bool,
) -> tuple[Emission, ...]:
    before = () if previous is None else previous
    if before == current:
        return ()
    # Pool IDs recur each day; the UTC day is part of a gift's identity.
    old = {(gift.day.date(), gift.id): gift for gift in before}
    emissions = [Emission("store_daily_gifts_changed", (before, current))]
    for gift in current:
        known = old.get((gift.day.date(), gift.id))
        if known is None:
            emissions.append(Emission("store_daily_gift", (gift,)))
        elif known != gift:
            emissions.append(Emission("store_daily_gift_updated", (known, gift)))
    return tuple(emissions)


@dataclass(frozen=True, slots=True)
class TrendingGameState:
    day: datetime
    game: str


def trending_allowed(previous: TrendingGameState, current: TrendingGameState) -> bool:
    return current.day >= previous.day


def trending_changes(
    previous: TrendingGameState | None,
    current: TrendingGameState,
    initial: bool,
) -> tuple[Emission, ...]:
    if previous is None or current.day > previous.day:
        return (Emission("stream_trending_game", (current.game,)),)
    if current.game != previous.game:
        return (
            Emission("stream_trending_game_updated", (previous.game, current.game)),
        )
    return ()
