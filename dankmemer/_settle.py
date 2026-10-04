from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeGuard, TypeVar, cast

_T = TypeVar("_T")


class _Identified(Protocol):
    @property
    def id(self) -> object: ...


def settle(previous: object, current: _T, tolerance: timedelta) -> _T:
    """Reuse ``previous`` timestamps that ``current`` matches within ``tolerance``.

    Some API timestamps are derived per request and differ by a millisecond
    between otherwise identical responses. Settling a new snapshot against the
    saved baseline keeps exact change detection meaningful. Because matches
    keep the baseline's value, gradual drift is still measured from it.
    Collections are matched by ``id`` when every entry has one, otherwise by
    position when their lengths agree. Unrecognised values are left unchanged.
    """
    if tolerance <= timedelta(0):
        return current
    return cast(_T, _settle(previous, current, tolerance))


def _settle(previous: object, current: object, tolerance: timedelta) -> object:
    if previous is current:
        return current
    if isinstance(current, datetime):
        if isinstance(previous, datetime) and abs(current - previous) <= tolerance:
            return previous
        return current
    if isinstance(current, tuple):
        values = cast(tuple[object, ...], current)
        if not isinstance(previous, tuple):
            return values
        return _settle_tuple(cast(tuple[object, ...], previous), values, tolerance)
    if is_dataclass(current) and not isinstance(current, type):
        if type(previous) is not type(current):
            return current
        changes: dict[str, Any] = {}
        for item in fields(current):
            if not item.init:
                continue
            new = getattr(current, item.name)
            settled = _settle(getattr(previous, item.name), new, tolerance)
            if settled is not new:
                changes[item.name] = settled
        if not changes:
            return current
        result = replace(current, **changes)
        return previous if result == previous else result
    return current


def _settle_tuple(
    previous: tuple[object, ...], current: tuple[object, ...], tolerance: timedelta
) -> tuple[object, ...]:
    if _identified(previous) and _identified(current):
        known: dict[object, _Identified] = {}
        for entry in previous:
            known.setdefault(entry.id, entry)
        settled = tuple(
            _settle(known[entry.id], entry, tolerance) if entry.id in known else entry
            for entry in current
        )
    elif len(previous) == len(current):
        settled = tuple(
            _settle(before, after, tolerance)
            for before, after in zip(previous, current, strict=True)
        )
    else:
        return current
    if all(a is b for a, b in zip(settled, current, strict=True)):
        return current
    return previous if settled == previous else settled


def _identified(values: tuple[object, ...]) -> TypeGuard[tuple[_Identified, ...]]:
    return bool(values) and all(
        is_dataclass(value) and hasattr(value, "id") for value in values
    )
