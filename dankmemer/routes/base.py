import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from rapidfuzz import fuzz

from dankmemer.types import NumericFilterType, StringFilterType
from dankmemer.utils import IN, Above, Below, Fuzzy, Range

if TYPE_CHECKING:
    from datetime import timedelta

    from dankmemer.client import DankMemerClient


CacheData = TypeVar("CacheData")


class CachedRoute(Generic[CacheData]):
    """Shared cache handling for DankAlert route wrappers."""

    def __init__(
        self,
        client: "DankMemerClient",
        cache_ttl: "timedelta | None",
    ) -> None:
        self.client: "DankMemerClient" = client
        self.cache_ttl: "timedelta | None" = cache_ttl
        self._cache: CacheData | None = None
        self._last_update: datetime | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _fetch(self) -> CacheData:
        raise NotImplementedError

    def _store_cache(self, data: CacheData) -> CacheData:
        if self.cache_ttl is None:
            return data
        self._cache = data
        self._last_update = datetime.now(timezone.utc)
        return data

    def _cache_is_expired(self) -> bool:
        if self.cache_ttl is None:
            return True
        if self._cache is None or self._last_update is None:
            return True
        return datetime.now(timezone.utc) - self._last_update > self.cache_ttl

    def clear_cache(self) -> None:
        """Clear this route's cached response data."""
        self._cache = None
        self._last_update = None

    def cache_info(self) -> dict[str, Any]:
        """Return lightweight cache state for this route."""
        return {
            "enabled": self.cache_ttl is not None,
            "has_value": self._cache is not None,
            "last_update": self._last_update,
            "ttl": self.cache_ttl,
        }

    async def _get_data(self) -> CacheData:
        async with self._lock:
            if self._cache_is_expired():
                return await self._fetch()
            return self._cache


def matches_string(field_value: str, filter_val: StringFilterType) -> bool:
    if not field_value:
        return False
    if isinstance(filter_val, Fuzzy):
        score: float = fuzz.ratio(field_value.lower(), filter_val.value.lower())
        return score >= filter_val.cutoff
    if isinstance(filter_val, IN):
        field_value_lower = field_value.lower()
        return any(
            pattern.lower() in field_value_lower for pattern in filter_val.patterns
        )
    return field_value.lower() == filter_val.lower()


def _coerce_numeric(value):
    if isinstance(value, (int, float)):
        return value
    return float(value)


def matches_numeric(field_value, filter_val: NumericFilterType) -> bool:
    if field_value is None:
        return False
    try:
        numeric_value = _coerce_numeric(field_value)
    except (ValueError, TypeError):
        return False

    if isinstance(filter_val, tuple):
        low, high = filter_val
        return low <= numeric_value <= high
    if isinstance(filter_val, Above):
        return numeric_value > filter_val.threshold
    if isinstance(filter_val, Below):
        return numeric_value < filter_val.threshold
    if isinstance(filter_val, Range):
        return filter_val.low <= numeric_value <= filter_val.high

    try:
        return numeric_value == _coerce_numeric(filter_val)
    except (ValueError, TypeError):
        return False


def matches_list(field_list: list[object], filter_val: StringFilterType) -> bool:
    for element in field_list:
        if isinstance(element, str) and matches_string(element, filter_val):
            return True
        if element == filter_val:
            return True
    return False
