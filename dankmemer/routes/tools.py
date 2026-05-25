from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Union

from dankmemer.types import (
    BooleanType,
    IntegerType,
    NumericFilterType,
    StringFilterType,
    StringType,
)
from dankmemer.routes.base import CachedRoute, matches_numeric, matches_string
from dankmemer.utils import IN as IN, DotDict, Fuzzy as Fuzzy

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Tool:
    """
    Represents an individual tool entry from the DankAlert API.

    Attributes:
        id (str): Unique identifier for the tool.
        name (str): Name of the tool.
        imageURL (str): URL of the tool's image.
        extra (DotDict): Additional tool data, which may include:
            - baits (bool): Whether the tool supports bait usage.
            - buffs (List[dict]): List of buffs provided by the tool.
            - debuffs (List[dict]): List of debuffs associated with the tool.
            - flavor (str): A descriptive string of the tool.
            - usage (int): The usage value for the tool.
    """
    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tool":
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
        Fallback attribute lookup: if an attribute is not found on the Tool instance,
        retrieve it from the extra data.
        """
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(f"'Tool' object has no attribute '{attribute}'")


class ToolsFilter:
    """
    Filter for /tools data.

    Filterable attributes:
      - id (str): Exact match.
      - name (StringFilterType): Matches the tool's name.
      - imageURL (StringFilterType): Matches the tool's imageURL.
      - flavor (StringFilterType): Matches extra.flavor.
      - baits (BooleanType): Matches extra.baits.
      - buffs (StringFilterType): Checks if any buff in extra.buffs matches.
      - debuffs (StringFilterType): Checks if any debuff in extra.debuffs matches.
      - usage (NumericFilterType): Matches extra.usage.
      - limit (int): Maximum number of results.

    Examples:
        .. code-block:: python
        
            from dankmemer import ToolsFilter, Fuzzy, IN
            filter_exact = ToolsFilter(name="Dynamite")
            filter_fuzzy = ToolsFilter(name=Fuzzy("dynamite", cutoff=80))
            filter_in = ToolsFilter(name=IN("fishing"))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        flavor: StringFilterType = None,
        baits: BooleanType = None,
        buffs: StringFilterType = None,
        debuffs: StringFilterType = None,
        usage: NumericFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.flavor: StringFilterType = flavor
        self.baits: BooleanType = baits
        self.buffs: StringFilterType = buffs
        self.debuffs: StringFilterType = debuffs
        self.usage: NumericFilterType = usage
        self.limit: IntegerType = limit

    def apply(self, data: List[Tool]) -> List[Tool]:
        results: List[Tool] = []
        for tool in data:
            if self.id is not None and tool.id != self.id:
                continue
            if self.name is not None and not self._matches_field(tool.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(tool.imageURL, self.imageURL):
                continue

            tool_flavor = tool.extra.get("flavor", "")
            if self.flavor is not None and not self._matches_field(tool_flavor, self.flavor):
                continue

            tool_baits = tool.extra.get("baits")
            if self.baits is not None and tool_baits != self.baits:
                continue

            tool_buffs = tool.extra.get("buffs", [])
            if self.buffs is not None:
                if not any(self._matches_field(buff.get("name", ""), self.buffs) for buff in tool_buffs):
                    continue

            tool_debuffs = tool.extra.get("debuffs", [])
            if self.debuffs is not None:
                if not any(self._matches_field(debuff.get("name", ""), self.debuffs) for debuff in tool_debuffs):
                    continue

            tool_usage = tool.extra.get("usage")
            if self.usage is not None:
                if tool_usage is None or not self._matches_numeric(tool_usage, self.usage):
                    continue
            results.append(tool)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)

    def _matches_numeric(self, field_value: Union[int, float], filter_val: NumericFilterType) -> bool:
        return matches_numeric(field_value, filter_val)


class ToolsRoute(CachedRoute[Dict[str, Tool]]):
    """
    Represents the /tools endpoint. This class converts raw API tool data into Tool objects,
    caches the results for a given time-to-live (TTL), and provides query and iteration methods.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Tool]:
        """
        Fetches and processes raw tool data from the "tools" API endpoint.
        Expects the API to return a dictionary keyed by tool IDs.
        """
        raw_data: Dict[str, Any] = await self.client.request("tools")
        processed: Dict[str, Tool] = {}
        for key, value in raw_data.items():
            processed[key] = Tool.from_dict(value)
        return self._store_cache(processed)

    async def query(self, tools_filter: Optional[ToolsFilter] = None) -> List[Tool]:
        """
        Retrieves a list of Tool objects from the /tools endpoint.
        If a ToolsFilter is provided, returns only the tools matching the filter criteria.

        :param tools_filter: Optional ToolsFilter instance with filtering criteria.
        :return: A list of Tool objects.
        """
        raw_dict: Dict[str, Tool] = await self._get_data()
        tool_list: List[Tool] = list(raw_dict.values())
        if tools_filter is not None:
            tool_list = tools_filter.apply(tool_list)
        return tool_list

    async def iter_query(self, tools_filter: Optional[ToolsFilter] = None) -> AsyncIterator[Tool]:
        """
        Asynchronously iterates over Tool objects from the /tools endpoint.
        If a ToolsFilter is provided, only iterates over tools matching the criteria.

        :param tools_filter: Optional ToolsFilter instance for filtering.
        :yield: Tool objects.
        """
        raw_dict: Dict[str, Tool] = await self._get_data()
        tool_list: List[Tool] = list(raw_dict.values())
        if tools_filter is not None:
            tool_list = tools_filter.apply(tool_list)
        for tool in tool_list:
            yield tool
