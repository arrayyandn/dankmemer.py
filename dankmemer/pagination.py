from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

_T_co = TypeVar("_T_co", covariant=True)


@dataclass(frozen=True, slots=True)
class Page(Generic[_T_co]):
    """One page returned by a resource's ``fetch`` method.

    Attributes
    ----------
    items: tuple
        Records in the order returned by the API.
    next_cursor: Optional[int]
        Offset to pass to the next ``fetch`` call, or ``None`` at the end.
        Offsets can change when entries are added or removed. A cursor does
        not refer to a fixed snapshot or a historical lookup.
    """

    items: tuple[_T_co, ...]
    next_cursor: int | None

    @property
    def has_more(self) -> bool:
        """Whether the API supplied an offset for another page."""
        return self.next_cursor is not None
