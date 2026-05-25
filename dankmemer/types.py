from typing import Any

from .utils import Fuzzy, IN, Above, Below, Range

StringFilterType = str | Fuzzy | IN | None

NumericFilterType = (
    int
    | float
    | tuple[int | float, int | float]
    | Above
    | Below
    | Range
    | None
)

StringType = str | None

BooleanType = bool | None

IntegerType = int | None

DictType = dict[str, Any] | None
