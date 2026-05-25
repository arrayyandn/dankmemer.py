from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import (
    IntegerType,
    NumericFilterType,
    StringFilterType,
    StringType,
)
from dankmemer.routes.base import CachedRoute, matches_list, matches_numeric, matches_string
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
class NPC:
    """
    Represents an NPC from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the NPC.
        name (str): The NPC's display name.
        imageURL (str): The URL of the NPC's image.
        extra (Dict[str, Any]): Additional data (including bio, emotions,
                                locations, nickname, reputation, rewards).
                                Access via dot notation is supported.
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, key: str, data: Dict[str, Any]) -> "NPC":
        extra_data = data.get("extra", {})
        # Wrap extra_data in DotDict to allow dot-notation access.
        if not isinstance(extra_data, DotDict):
            extra_data = DotDict(extra_data)
        return cls(
            id=data.get("id", key),
            name=data.get("name", ""),
            imageURL=data.get("imageURL", ""),
            extra=extra_data,
        )

    def __getattr__(self, attribute: str) -> Any:
        """
        Fallback attribute lookup: if an attribute is not found normally,
        attempt to retrieve it from the extra data.
        """
        # This allows accessing extra attributes directly via npc.attribute
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(f"'NPC' object has no attribute '{attribute}'")


class NPCsFilter:
    """
    Filter for /npcs data.

    You can filter on:
      - id: (str) exact match.
      - name: (str or Fuzzy) string matching (fuzzy matching if wrapped in Fuzzy).
      - imageURL: (str or Fuzzy) match on image URL.
      - bio: (str or Fuzzy) fuzzy/exact match applied to the NPC's extra.bio.
      - nickname: (str or Fuzzy) match on the extra.nickname field.
      - reputation: (int or Tuple[int,int]) numeric filtering on extra.reputation.
      - locations: (str or Fuzzy) matches if any location in extra.locations matches.
      - limit: (int) maximum number of results.

    Examples:
        .. code-block:: python

            from dankmemer import NPCsFilter, Fuzzy, IN, Above, Below, Range

            # Exact string matching for the 'name' field.
            filter_exact = NPCsFilter(name="Chad")

            # Fuzzy matching example for the 'name' field:
            filter_fuzzy = NPCsFilter(name=Fuzzy("chad", cutoff=75))

            # Using IN for membership matching.
            filter_in = NPCsFilter(name=IN("chad", "brad"))

            # Numeric filtering: filtering 'reputation' for an exact match.
            filter_numeric = NPCsFilter(reputation=30)

            # Numeric filtering with a tuple.
            filter_range = NPCsFilter(reputation=(10, 50))

            # Numeric filtering with interfaces.
            filter_above = NPCsFilter(reputation=Above(20))
            filter_below = NPCsFilter(reputation=Below(50))
            filter_range_interface = NPCsFilter(reputation=Range(10, 50))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        bio: StringFilterType = None,
        nickname: StringFilterType = None,
        reputation: NumericFilterType = None,
        locations: StringFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: Optional[str] = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.bio: StringFilterType = bio
        self.nickname: StringFilterType = nickname
        self.reputation: NumericFilterType = reputation
        self.locations: StringFilterType = locations
        self.limit: IntegerType = limit

    def apply(self, data: List[NPC]) -> List[NPC]:
        results: List[NPC] = []
        for npc in data:
            if self.id is not None and npc.id != self.id:
                continue
            if self.name is not None and not self._matches_field(npc.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(
                npc.imageURL, self.imageURL
            ):
                continue

            extra = npc.extra

            if self.bio is not None:
                bio_val = extra.get("bio", "")
                if not self._matches_field(bio_val, self.bio):
                    continue

            if self.nickname is not None:
                nick = extra.get("nickname", "")
                if not self._matches_field(nick, self.nickname):
                    continue

            if self.reputation is not None:
                rep = extra.get("reputation")
                if not self._matches_numeric(rep, self.reputation):
                    continue

            if self.locations is not None:
                locs = extra.get("locations", [])
                if not self._matches_list(locs, self.locations):
                    continue

            results.append(npc)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)

    def _matches_numeric(self, field_value: Any, filter_val: NumericFilterType) -> bool:
        return matches_numeric(field_value, filter_val)

    def _matches_list(
        self, field_list: List[Any], filter_val: StringFilterType
    ) -> bool:
        """
        For list fields (e.g. locations), if any element in the list matches the filter criterion,
        we consider it a match.
        """
        return matches_list(field_list, filter_val)


class NPCsRoute(CachedRoute[Dict[str, NPC]]):
    """
    Represents the /npcs endpoint, converting raw API data into Python objects and
    providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, NPC]:
        raw_data: Dict[str, Any] = await self.client.request("npcs")
        processed: Dict[str, NPC] = {}
        for key, value in raw_data.items():
            npc = NPC.from_dict(key, value)
            processed[npc.id] = npc
        return self._store_cache(processed)

    async def query(self, npc_filter: Optional[NPCsFilter] = None) -> List[NPC]:
        """
        Retrieve a list of NPCs from the /npcs endpoint.

        If no filter is provided, all NPCs are returned. Otherwise, only the NPCs matching
        the provided filter criteria are returned.
        """
        raw_dict: Dict[str, NPC] = await self._get_data()
        npc_list: List[NPC] = list(raw_dict.values())
        if npc_filter is None:
            return npc_list
        return npc_filter.apply(npc_list)

    async def iter_query(
        self, npc_filter: Optional[NPCsFilter] = None
    ) -> AsyncIterator[NPC]:
        """
        Asynchronously iterates over NPCs from the /npcs endpoint.

        If an NPCsFilter is provided, only NPCs matching the filter criteria are yielded.

        Yields:
            Each NPC object from the query results.
        """
        raw_dict: Dict[str, NPC] = await self._get_data()
        npc_list: List[NPC] = list(raw_dict.values())
        if npc_filter is not None:
            npc_list = npc_filter.apply(npc_list)
        for npc in npc_list:
            yield npc
