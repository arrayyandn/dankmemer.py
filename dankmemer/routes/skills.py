from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Union

from dankmemer.types import (
    IntegerType,
    NumericFilterType,
    StringFilterType,
)
from dankmemer.routes.base import CachedRoute, matches_numeric, matches_string
from dankmemer.utils import (
    IN as IN,
    Above as Above,
    Below as Below,
    Fuzzy as Fuzzy,
    Range as Range,
)

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Skill:
    """
    Represents a skill from the DankAlert API.

    Attributes:
        name (str): The name of the skill.
        tiers (int): The number of tiers available for the skill.
    """

    name: str
    tiers: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Skill":
        return cls(name=data.get("name", ""), tiers=data.get("tiers", 0))


class SkillsFilter:
    """
    Filter for skills data.

    Supports filtering by:

      - **name**: A string matching filter (exact, fuzzy via `Fuzzy`, or membership using `IN`).
      - **tiers**: A numeric filter (exact, a tuple range, or using interfaces such as `Above`, `Below`, or `Range`).
      - **limit**: Optionally limit the number of results returned.

    **Examples**:
        .. code-block:: python

            from dankmemer import SkillsFilter, Fuzzy, IN, Above, Below, Range

            # Filter by exact skill name.
            filter_exact = SkillsFilter(name="AFK")

            # Fuzzy filtering for skill names.
            filter_fuzzy = SkillsFilter(name=Fuzzy("carto", cutoff=80))

            # Membership filtering for skills containing "angler" or "hunter"
            filter_in = SkillsFilter(name=IN("angler", "hunter"))

            # Filtering by tiers: skills with more than 5 tiers.
            filter_tiers = SkillsFilter(tiers=Above(5))
    """

    def __init__(
        self,
        name: StringFilterType = None,
        tiers: NumericFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.name: StringFilterType = name
        self.tiers: NumericFilterType = tiers
        self.limit: IntegerType = limit

    def apply(self, data: List[Skill]) -> List[Skill]:
        results: List[Skill] = []
        for skill in data:
            if self.name is not None and not self._matches_field(skill.name, self.name):
                continue
            if self.tiers is not None and not self._matches_numeric(
                skill.tiers, self.tiers
            ):
                continue
            results.append(skill)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)

    def _matches_numeric(
        self, field_value: Union[int, float], filter_val: NumericFilterType
    ) -> bool:
        return matches_numeric(field_value, filter_val)


class SkillsRoute(CachedRoute[Dict[str, Skill]]):
    """
    Represents the /skills endpoint, converting raw API data into Skill objects and
    providing filtering functionality.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Skill]:
        raw_data: Dict[str, Any] = await self.client.request("skills")
        processed: Dict[str, Skill] = {}
        for key, value in raw_data.items():
            processed[key] = Skill.from_dict(value)
        return self._store_cache(processed)

    async def query(self, skill_filter: Optional[SkillsFilter] = None) -> List[Skill]:
        """
        Retrieve a list of Skill objects from the /skills endpoint.

        If a SkillsFilter is provided, only the skills matching the filter criteria are returned.

        :param skill_filter: An optional SkillsFilter instance.
        :return: A list of Skill objects.
        """
        data = await self._get_data()
        skills_list = list(data.values())
        if skill_filter is not None:
            skills_list = skill_filter.apply(skills_list)
        return skills_list

    async def iter_query(
        self, skill_filter: Optional[SkillsFilter] = None
    ) -> AsyncIterator[Skill]:
        """
        Asynchronously iterates over Skill objects from the /skills endpoint.

        If a SkillsFilter is provided, only skills matching the filter criteria are yielded.

        :param skill_filter: An optional SkillsFilter instance.
        :yield: Skill objects one by one.
        """
        data = await self._get_data()
        skills_list = list(data.values())
        if skill_filter is not None:
            skills_list = skill_filter.apply(skills_list)
        for skill in skills_list:
            yield skill
