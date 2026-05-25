from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import (
    BooleanType,
    IntegerType,
    StringFilterType,
    StringType,
)
from dankmemer.routes.base import CachedRoute, matches_string
from dankmemer.utils import IN as IN, DotDict, Fuzzy as Fuzzy

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Location:
    """
    Represents a location obtained from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the location.
        name (str): The name of the location.
        imageURL (str): The URL of the location's image.
        extra (DotDict): A dictionary of additional location data, typically including:
            - bannerURL (str): URL for the banner image.
            - creatures (List[str]): List of creature IDs available at this location.
            - days (List[int]): List of days (0-6) when the location is available.
            - disabled (bool): Whether the location is disabled.
            - failChance (int): Chance of failure.
            - mineChance (int): Chance for mining.
            - mythicalFish (List[str]): List of mythical fish available.
            - npcs (List[str]): List of NPC names available.
            - temporary (bool): Whether the location is temporary.
            - thumbnailURL (str): URL for the thumbnail image.
        rarityFish (Dict[str, List[str]]): A mapping of rarity categories to lists of fish.
        variantsData (Dict[str, Any]): Data on variant multipliers (e.g., "chroma", "high quality", etc.)
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict
    rarityFish: Dict[str, List[str]]
    variantsData: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Location":
        extra_data = data.get("extra", {})
        if not isinstance(extra_data, DotDict):
            extra_data = DotDict(extra_data)
        return cls(
            id=data.get("id"),
            name=data.get("name"),
            imageURL=data.get("imageURL"),
            extra=extra_data,
            rarityFish=data.get("rarityFish", {}),
            variantsData=data.get("variantsData", {}),
        )

    def __getattr__(self, attribute: str) -> Any:
        """
        Fallback attribute lookup: if an attribute is not found normally,
        attempt to retrieve it from the extra data.
        """
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(f"'Location' object has no attribute '{attribute}'")


class LocationsFilter:
    """
    Filter for /locations data.

    You can filter on:
      - id: (str) exact match.
      - name: (StringFilterType) string matching (exact, fuzzy using Fuzzy, or membership using IN).
      - imageURL: (StringFilterType) matching on the location's image URL.
      - bannerURL: (StringFilterType) filtering applied to extra.bannerURL.
      - disabled: (BooleanType) filter by the 'disabled' flag in extra.
      - temporary: (BooleanType) filter by the 'temporary' flag in extra.
      - limit: (int) maximum number of results returned.

    Examples:
        .. code-block:: python

            from dankmemer import LocationsFilter, Fuzzy, IN

            # Exact matching.
            filter_exact = LocationsFilter(name="Camp Guillermo")

            # Fuzzy matching.
            filter_fuzzy = LocationsFilter(name=Fuzzy("camp", cutoff=80))

            # Membership matching.
            filter_in = LocationsFilter(name=IN("ocean"))

            # Boolean filtering.
            filter_disabled = LocationsFilter(disabled=True)
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        bannerURL: StringFilterType = None,
        disabled: BooleanType = None,
        temporary: BooleanType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.bannerURL: StringFilterType = bannerURL
        self.disabled: BooleanType = disabled
        self.temporary: BooleanType = temporary
        self.limit: IntegerType = limit

    def apply(self, data: List[Location]) -> List[Location]:
        results: List[Location] = []
        for loc in data:
            if self.id is not None and loc.id != self.id:
                continue
            if self.name is not None and not self._matches_field(loc.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(
                loc.imageURL, self.imageURL
            ):
                continue
            extra = loc.extra
            if self.bannerURL is not None:
                banner = extra.get("bannerURL", "")
                if not self._matches_field(banner, self.bannerURL):
                    continue
            if self.disabled is not None:
                disabled_val = extra.get("disabled")
                if disabled_val != self.disabled:
                    continue
            if self.temporary is not None:
                temporary_val = extra.get("temporary")
                if temporary_val != self.temporary:
                    continue
            results.append(loc)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)


class LocationsRoute(CachedRoute[Dict[str, Location]]):
    """
    Represents the /locations endpoint, converting raw API data into Location objects and
    providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Location]:
        raw_data: Dict[str, Any] = await self.client.request("locations")
        processed: Dict[str, Location] = {}
        for key, value in raw_data.items():
            processed[key] = Location.from_dict(value)
        return self._store_cache(processed)

    async def query(
        self, locations_filter: Optional[LocationsFilter] = None
    ) -> List[Location]:
        """
        Retrieve the list of Location objects from the /locations endpoint.

        If a LocationsFilter is provided, only locations matching the criteria are returned.

        :param locations_filter: Optional LocationsFilter instance with filtering criteria.
        :return: A list of Location objects.
        """
        raw_dict: Dict[str, Location] = await self._get_data()
        locations_list: List[Location] = list(raw_dict.values())
        if locations_filter is not None:
            locations_list = locations_filter.apply(locations_list)
        return locations_list

    async def iter_query(
        self, locations_filter: Optional[LocationsFilter] = None
    ) -> AsyncIterator[Location]:
        """
        Asynchronously iterates over Location objects from the /locations endpoint.

        If a LocationsFilter is provided, only locations matching the criteria are yielded.

        :param locations_filter: Optional LocationsFilter instance for filtering.
        :yield: Each Location object from the query results.
        """
        raw_dict: Dict[str, Location] = await self._get_data()
        locations_list: List[Location] = list(raw_dict.values())
        if locations_filter is not None:
            locations_list = locations_filter.apply(locations_list)
        for location in locations_list:
            yield location
