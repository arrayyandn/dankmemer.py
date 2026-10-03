from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TypeVar, cast

from rapidfuzz.fuzz import WRatio

from dankmemer.errors import ConfigurationError

_T = TypeVar("_T")


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a nonempty string")
    return value.strip()


def normalise(value: str) -> str:
    return value.strip().casefold()


def model(value: object, expected: type[_T], name: str) -> _T:
    if not isinstance(value, expected):
        raise ConfigurationError(f"{name} must be {expected.__name__}")
    return value


def integer(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ConfigurationError(f"{name} must be an integer of at least {minimum}")
    return value


def tags(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, tuple):
        raise ConfigurationError("tags must be a tuple of strings")
    return tuple(normalise(text(tag, "tag")) for tag in cast(tuple[object, ...], value))


def optional_text(value: object | None, name: str) -> str | None:
    return None if value is None else normalise(text(value, name))


def boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ConfigurationError(f"{name} must be bool")
    return value


def optional_boolean(value: object | None, name: str) -> None:
    if value is not None:
        boolean(value, name)


def limit(value: object | None, name: str = "max_limit") -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ConfigurationError(f"{name} must be a nonnegative integer or None")
    return value


def integer_range(minimum: object | None, maximum: object | None, name: str) -> None:
    for value in (minimum, maximum):
        if value is not None and type(value) is not int:
            raise ConfigurationError(f"{name} bounds must be integers")
    if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
        raise ConfigurationError(f"{name} minimum must not exceed its maximum")


def within(value: int, minimum: int | None, maximum: int | None) -> bool:
    return (minimum is None or value >= minimum) and (
        maximum is None or value <= maximum
    )


def aware(value: object | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ConfigurationError(f"{name} must be a timezone-aware datetime")


def search_options(query: str, fuzzy: bool, min_score: object) -> str:
    query = normalise(text(query, "query"))
    boolean(fuzzy, "fuzzy")
    if (
        isinstance(min_score, bool)
        or not isinstance(min_score, (int, float))
        or not 0 <= min_score <= 100
        or not math.isfinite(min_score)
    ):
        raise ConfigurationError("min_score must be a finite number from 0 to 100")
    return query


def rank(
    records: Iterable[_T],
    query: str,
    name: Callable[[_T], str],
    *,
    fuzzy: bool,
    min_score: float,
    max_limit: int | None,
) -> tuple[_T, ...]:
    matches: list[tuple[float, str, int, _T]] = []
    for position, record in enumerate(records):
        candidate = normalise(name(record))
        if fuzzy:
            score = WRatio(query, candidate)
            if score < min_score:
                continue
        else:
            if query not in candidate:
                continue
            score = (
                100.0
                if query == candidate
                else 90.0
                if candidate.startswith(query)
                else 80.0
            )
        matches.append((-score, candidate, position, record))
    matches.sort(key=lambda match: match[:3])
    return tuple(match[3] for match in matches[:max_limit])


def select(
    records: Iterable[_T], predicate: Callable[[_T], bool], max_limit: int | None
) -> tuple[_T, ...]:
    if max_limit == 0:
        return ()
    matches: list[_T] = []
    for record in records:
        if predicate(record):
            matches.append(record)
            if max_limit is not None and len(matches) >= max_limit:
                break
    return tuple(matches)
