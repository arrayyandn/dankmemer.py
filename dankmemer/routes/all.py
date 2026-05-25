from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional, Tuple

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient

from .base import CachedRoute
from .baits import BaitsFilter, Bait
from .buckets import BucketsFilter, Bucket
from .creatures import CreaturesFilter, Creature
from .decorations import DecorationsFilter, Decoration
from .events import EventsFilter, Event
from .items import ItemsFilter, Item
from .locations import LocationsFilter, Location
from .npcs import NPCsFilter, NPC
from .seasons import SeasonsFilter, Season
from .skills import SkillsFilter, Skill
from .skillsdata import SkillDataFilter, SkillData
from .tanks import TanksFilter, Tank
from .tools import ToolsFilter, Tool

class AllFilter:
    """
    Aggregated filter for the /all endpoint.

    Each attribute is optional and accepts a filter object for the corresponding route.

    Examples:
        .. code-block:: python
        
            from dankmemer import (
                AllFilter, BaitsFilter, BucketsFilter, CreaturesFilter, DecorationsFilter,
                EventsFilter, ItemsFilter, LocationsFilter, NPCsFilter, SeasonsFilter,
                SkillsFilter, SkillDataFilter, TanksFilter, ToolsFilter, Fuzzy, IN
            )

            filter_all = AllFilter(
                baits=BaitsFilter(name="Deadly Bait"),
                buckets=BucketsFilter(name=IN("bucket")),
                creatures=CreaturesFilter(name=Fuzzy("ahuitzotl", cutoff=75)),
                decorations=DecorationsFilter(name="Alien Planet"),
                events=EventsFilter(name=Fuzzy("quality time", cutoff=80)),
                items=ItemsFilter(name="Trash"),
                locations=LocationsFilter(name=IN("camp")),
                npcs=None,  # No filtering on NPCs, for example.
                seasons=SeasonsFilter(active=True),
                skills=SkillsFilter(name="AFK"),
                skillsdata=SkillDataFilter(category="Economy"),
                tanks=TanksFilter(name="Blue"),
                tools=ToolsFilter(name=Fuzzy("dynamite", cutoff=80))
            )
    """

    def __init__(
        self,
        baits: Optional[BaitsFilter] = None,
        buckets: Optional[BucketsFilter] = None,
        creatures: Optional[CreaturesFilter] = None,
        decorations: Optional[DecorationsFilter] = None,
        events: Optional[EventsFilter] = None,
        items: Optional[ItemsFilter] = None,
        locations: Optional[LocationsFilter] = None,
        npcs: Optional[NPCsFilter] = None,
        seasons: Optional[SeasonsFilter] = None,
        skills: Optional[SkillsFilter] = None,
        skillsdata: Optional[SkillDataFilter] = None,
        tanks: Optional[TanksFilter] = None,
        tools: Optional[ToolsFilter] = None,
        limit: Optional[int] = None,
    ) -> None:
        self.baits = baits
        self.buckets = buckets
        self.creatures = creatures
        self.decorations = decorations
        self.events = events
        self.items = items
        self.locations = locations
        self.npcs = npcs
        self.seasons = seasons
        self.skills = skills
        self.skillsdata = skillsdata
        self.tanks = tanks
        self.tools = tools
        self.limit = limit


class AllRoute(CachedRoute[Dict[str, Any]]):
    """
    Represents the /all endpoint.

    This route fetches aggregated data (as a JSON object with keys matching individual routes)
    from the API and applies filters from an AllFilter to each section.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Any]:
        raw_data: Dict[str, Any] = await self.client.request("all")
        return self._store_cache(raw_data)

    async def query(self, all_filter: Optional[AllFilter] = None) -> Dict[str, Any]:
        """
        Retrieves aggregated data from the /all endpoint and applies corresponding filters.

        :param all_filter: Optional AllFilter instance containing filtering criteria for each route.
        :return: A dictionary with keys such as "baits", "buckets", etc., containing filtered results.
        """
        data = await self._get_data()
        result: Dict[str, Any] = {}

        # For each individual route key, convert the data into a list if necessary and apply its filter.
        result["baits"] = [Bait.from_dict(entry) for entry in data.get("baits", {}).values()]
        if all_filter and all_filter.baits:
            result["baits"] = all_filter.baits.apply(result["baits"])

        result["buckets"] = [Bucket.from_dict(entry) for entry in data.get("buckets", {}).values()]
        if all_filter and all_filter.buckets:
            result["buckets"] = all_filter.buckets.apply(result["buckets"])

        result["creatures"] = [Creature.from_dict(entry) for entry in data.get("creatures", {}).values()]
        if all_filter and all_filter.creatures:
            result["creatures"] = all_filter.creatures.apply(result["creatures"])

        result["decorations"] = [Decoration.from_dict(entry) for entry in data.get("decorations", {}).values()]
        if all_filter and all_filter.decorations:
            result["decorations"] = all_filter.decorations.apply(result["decorations"])

        result["events"] = [Event.from_dict(entry) for entry in data.get("events", {}).values()]
        if all_filter and all_filter.events:
            result["events"] = all_filter.events.apply(result["events"])

        result["items"] = [Item.from_dict(entry) for entry in data.get("items", {}).values()]
        if all_filter and all_filter.items:
            result["items"] = all_filter.items.apply(result["items"])

        result["locations"] = [Location.from_dict(entry) for entry in data.get("locations", {}).values()]
        if all_filter and all_filter.locations:
            result["locations"] = all_filter.locations.apply(result["locations"])

        result["npcs"] = [NPC.from_dict(key, entry) for key, entry in data.get("npcs", {}).items()]
        if all_filter and all_filter.npcs:
            result["npcs"] = all_filter.npcs.apply(result["npcs"])

        result["seasons"] = [Season.from_dict(entry) for entry in data.get("seasons", {}).values()]
        if all_filter and all_filter.seasons:
            result["seasons"] = all_filter.seasons.apply(result["seasons"])

        result["skills"] = [Skill.from_dict(entry) for entry in data.get("skills", {}).values()]
        if all_filter and all_filter.skills:
            result["skills"] = all_filter.skills.apply(result["skills"])

        skillsdata = data.get("skillsdata", {})
        if isinstance(skillsdata, list):
            result["skillsdata"] = [SkillData.from_dict(entry) for entry in skillsdata]
        else:
            result["skillsdata"] = [SkillData.from_dict(entry) for entry in skillsdata.values()]
        if all_filter and all_filter.skillsdata:
            result["skillsdata"] = all_filter.skillsdata.apply(result["skillsdata"])

        result["tanks"] = [Tank.from_dict(entry) for entry in data.get("tanks", {}).values()]
        if all_filter and all_filter.tanks:
            result["tanks"] = all_filter.tanks.apply(result["tanks"])

        result["tools"] = [Tool.from_dict(entry) for entry in data.get("tools", {}).values()]
        if all_filter and all_filter.tools:
            result["tools"] = all_filter.tools.apply(result["tools"])

        # If an overall limit is specified in the AllFilter, apply it to each route's result.
        if all_filter and all_filter.limit is not None:
            for key in result:
                result[key] = result[key][: all_filter.limit]

        return result

    async def iter_query(
        self, all_filter: Optional[AllFilter] = None
    ) -> AsyncIterator[Tuple[str, Any]]:
        """
        Asynchronously iterates over the aggregated data.

        :param all_filter: Optional AllFilter instance for filtering criteria.
        :yield: Tuples where the first element is the route key and the second is the corresponding filtered list.
        """
        result = await self.query(all_filter)
        for route, data in result.items():
            yield route, data
