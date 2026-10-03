from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from .errors import ConfigurationError

_T = TypeVar("_T")


def checked_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ConfigurationError(f"{name} must be bool")
    return value


def _check_revision(value: object) -> None:
    if value is not None and type(value) is not int:
        raise ConfigurationError("observation revision must be an integer")


@dataclass(frozen=True, slots=True)
class Emission:
    """An event name and the positional arguments sent to its listeners."""

    event: str
    args: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class Observation(Generic[_T]):
    """The result of one attempt to read an event resource.

    ``value`` is the candidate state to compare with the last accepted state.
    The resource reader sets ``complete=True`` only after reading and validating
    every required page. An incomplete read is ignored because missing data
    could look like an ended event.
    ``revision`` is an optional ordering number if the source provides one;
    it is not an event ID or a way to retrieve history.
    """

    value: _T
    complete: bool
    revision: int | None = None

    def __post_init__(self) -> None:
        checked_bool(self.complete, "observation completeness")
        _check_revision(self.revision)


def prepare_observation(
    current: Observation[_T] | None,
    candidate: Observation[_T],
    derive: Callable[[_T | None, _T, bool], Sequence[Emission]],
    *,
    emit_initial: bool,
) -> tuple[Emission, ...] | None:
    """Derive changes without advancing the baseline; reject with ``None``."""
    if not candidate.complete:
        return None
    if current is not None and current.revision is not None:
        if candidate.revision is None or candidate.revision <= current.revision:
            return None
    if current is None and not emit_initial:
        return ()
    return tuple(
        derive(
            None if current is None else current.value,
            candidate.value,
            current is None,
        )
    )
