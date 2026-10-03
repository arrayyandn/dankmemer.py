from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

JSONValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)
