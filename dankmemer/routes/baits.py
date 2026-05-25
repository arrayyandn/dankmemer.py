from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import (
    BooleanType,
    IntegerType,
    NumericFilterType,
    StringFilterType,
    StringType,
)
from dankmemer.routes.base import CachedRoute, matches_numeric, matches_string
from dankmemer.utils import (
    IN as IN,
    Above as Above,
    Below as Below,
    DotDict,
    Fuzzy as Fuzzy,
    Range as Range,
)

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Bait:
    """
    Represents a bait from the DankAlert API.

    Attributes:
        id (str): The unique identifier of the bait.
        name (str): The name of the bait.
        imageURL (str): The URL pointing to the bait's image.
        extra (Dict[str, Any]): Additional bait data, including:
            - explanation (str): Explanation of the bait's effects.
            - flavor (str): Flavor text.
            - idle (bool): Whether the bait is considered idle.
            - usage (int): The usage value of the bait.
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bait":
        extra_data = data.get("extra", {})
        if not isinstance(extra_data, DotDict):
            extra_data = DotDict(extra_data)
        return cls(
            id=data.get("id"),
            name=data.get("name"),
            imageURL=data.get("imageURL"),
            extra=extra_data,
        )
    
    def __getattr__(self, attribute: str) -> Any:
        """
        Fallback attribute lookup: if an attribute is not found normally,
        attempt to retrieve it from the extra data.
        """
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(f"'Bait' object has no attribute '{attribute}'")


class BaitsFilter:
    """
    Filter for /baits data.

    You can filter on:
      - id: (str) exact match.
      - name: (StringFilterType) string matching (exact, fuzzy using Fuzzy or membership using IN).
      - imageURL: (StringFilterType) matching on the bait image URL.
      - explanation: (StringFilterType) filter applied on extra.explanation.
      - flavor: (StringFilterType) filter applied on extra.flavor.
      - idle: (BooleanType) filter by the 'idle' flag in extra.
      - usage: (NumericFilterType) numeric filtering on extra.usage.
      - limit: (int) maximum number of results.

    Examples:
        .. code-block:: python

            from dankmemer import BaitsFilter, Fuzzy, IN, Above, Below, Range

            # Exact string matching for bait name.
            filter_exact = BaitsFilter(name="Golden Bait")

            # Fuzzy matching for bait name.
            filter_fuzzy = BaitsFilter(name=Fuzzy("golden", cutoff=80))

            # Membership matching using IN for bait name.
            filter_in = BaitsFilter(name=IN("golden", "eyeball"))

            # Numeric filtering on usage.
            filter_usage = BaitsFilter(usage=Above(5))

            # Filtering by idle flag.
            filter_idle = BaitsFilter(idle=True)
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        explanation: StringFilterType = None,
        flavor: StringFilterType = None,
        idle: BooleanType = None,
        usage: NumericFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.explanation: StringFilterType = explanation
        self.flavor: StringFilterType = flavor
        self.idle: BooleanType = idle
        self.usage: NumericFilterType = usage
        self.limit: IntegerType = limit

    def apply(self, data: List[Bait]) -> List[Bait]:
        results: List[Bait] = []
        for bait in data:
            if self.id is not None and bait.id != self.id:
                continue
            if self.name is not None and not self._matches_field(bait.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(
                bait.imageURL, self.imageURL
            ):
                continue

            extra = bait.extra
            if self.explanation is not None:
                expl = extra.get("explanation", "")
                if not self._matches_field(expl, self.explanation):
                    continue
            if self.flavor is not None:
                flav = extra.get("flavor", "")
                if not self._matches_field(flav, self.flavor):
                    continue
            if self.idle is not None:
                idle_val = extra.get("idle")
                if idle_val != self.idle:
                    continue
            if self.usage is not None:
                usage_val = extra.get("usage")
                if not self._matches_numeric(usage_val, self.usage):
                    continue
            results.append(bait)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)

    def _matches_numeric(self, field_value: Any, filter_val: NumericFilterType) -> bool:
        return matches_numeric(field_value, filter_val)


class BaitsRoute(CachedRoute[Dict[str, Bait]]):
    """
    Represents the /baits endpoint, converting raw API data into Bait objects and
    providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Bait]:
        raw_data: Dict[str, Any] = await self.client.request("baits")
        processed: Dict[str, Bait] = {}
        for key, value in raw_data.items():
            processed[key] = Bait.from_dict(value)
        return self._store_cache(processed)

    async def query(self, bait_filter: Optional[BaitsFilter] = None) -> List[Bait]:
        """
        Retrieve the list of Baits from the /baits endpoint.

        If no filter is provided, all baits are returned. Otherwise, only the baits matching
        the provided filter criteria are returned.

        :param bait_filter: Optional BaitsFilter instance containing filtering criteria.
        :return: A list of Bait objects.
        """
        raw_dict: Dict[str, Bait] = await self._get_data()
        baits_list: List[Bait] = list(raw_dict.values())
        if bait_filter is not None:
            baits_list = bait_filter.apply(baits_list)
        return baits_list

    async def iter_query(
        self, bait_filter: Optional[BaitsFilter] = None
    ) -> AsyncIterator[Bait]:
        """
        Asynchronously iterates over baits from the /baits endpoint.

        If a BaitsFilter is provided, only baits matching the filter criteria are yielded.

        Yields:
            Each Bait object from the query results.
        """
        raw_dict: Dict[str, Bait] = await self._get_data()
        baits_list: List[Bait] = list(raw_dict.values())
        if bait_filter is not None:
            baits_list = bait_filter.apply(baits_list)
        for bait in baits_list:
            yield bait
