from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import IntegerType, StringFilterType, StringType
from dankmemer.routes.base import CachedRoute, matches_string
from dankmemer.utils import IN as IN, DotDict, Fuzzy as Fuzzy

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Decoration:
    """
    Represents a decoration obtained from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the decoration.
        name (str): The name of the decoration.
        imageURL (str): The URL pointing to the decoration's image.
        extra (Dict[str, Any]): Additional decoration data, typically including:
            - flavor (str): A descriptive flavor text.
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Decoration":
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
        raise AttributeError(f"'Decoration' object has no attribute '{attribute}'")


class DecorationsFilter:
    """
    Filter for /decorations data.

    You can filter on:
      - id: (str) exact match.
      - name: (StringFilterType) string matching (exact, fuzzy via Fuzzy, or membership using IN).
      - imageURL: (StringFilterType) matching on the decoration image URL.
      - flavor: (StringFilterType) filtering applied to extra.flavor.
      - limit: (int) maximum number of results returned.

    Examples:
        .. code-block:: python

            from dankmemer import DecorationsFilter, Fuzzy, IN

            # Exact matching on name.
            filter_exact = DecorationsFilter(name="Alien Planet")

            # Fuzzy matching.
            filter_fuzzy = DecorationsFilter(name=Fuzzy("alien", cutoff=80))

            # Membership matching.
            filter_in = DecorationsFilter(name=IN("alien", "area-51"))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        flavor: StringFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.flavor: StringFilterType = flavor
        self.limit: IntegerType = limit

    def apply(self, data: List[Decoration]) -> List[Decoration]:
        results: List[Decoration] = []
        for deco in data:
            if self.id is not None and deco.id != self.id:
                continue
            if self.name is not None and not self._matches_field(deco.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(
                deco.imageURL, self.imageURL
            ):
                continue
            extra = deco.extra
            if self.flavor is not None:
                flav = extra.get("flavor", "")
                if not self._matches_field(flav, self.flavor):
                    continue
            results.append(deco)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)


class DecorationsRoute(CachedRoute[Dict[str, Decoration]]):
    """
    Represents the /decorations endpoint, converting raw API data into Decoration objects
    and providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Decoration]:
        raw_data: Dict[str, Any] = await self.client.request("decorations")
        processed: Dict[str, Decoration] = {}
        for key, value in raw_data.items():
            processed[key] = Decoration.from_dict(value)
        return self._store_cache(processed)

    async def query(
        self, deco_filter: Optional[DecorationsFilter] = None
    ) -> List[Decoration]:
        """
        Retrieve the list of Decoration objects from the /decorations endpoint.

        If a DecorationsFilter is provided, only decorations matching the filtering criteria are returned.

        :param deco_filter: Optional DecorationsFilter instance for filtering criteria.
        :return: A list of Decoration objects.
        """
        raw_dict: Dict[str, Decoration] = await self._get_data()
        deco_list: List[Decoration] = list(raw_dict.values())
        if deco_filter is not None:
            deco_list = deco_filter.apply(deco_list)
        return deco_list

    async def iter_query(
        self, deco_filter: Optional[DecorationsFilter] = None
    ) -> AsyncIterator[Decoration]:
        """
        Asynchronously iterates over Decoration objects from the /decorations endpoint.

        If a DecorationsFilter is provided, only decorations matching the criteria are yielded.

        :param deco_filter: Optional DecorationsFilter instance for filtering.
        :yield: Each Decoration object from the query results.
        """
        raw_dict: Dict[str, Decoration] = await self._get_data()
        deco_list: List[Decoration] = list(raw_dict.values())
        if deco_filter is not None:
            deco_list = deco_filter.apply(deco_list)
        for deco in deco_list:
            yield deco
