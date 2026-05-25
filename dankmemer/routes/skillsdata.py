from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import IntegerType, StringFilterType, StringType
from dankmemer.routes.base import CachedRoute, matches_string
from dankmemer.utils import IN as IN, DotDict, Fuzzy as Fuzzy

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class SkillData:
    """
    Represents an individual skill data entry obtained from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the skill.
        name (str): The display name of the skill.
        imageURL (str): The URL of the skill's image.
        extra (DotDict): A dictionary of additional skill data. This typically includes:
            - category (str): The skill category (e.g. "Economy", "Nature", etc.).
            - description (str): A description of the skill's effect.
            - requirements (dict): Requirements for the skill (badge and/or other skill requirements).
    """
    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillData":
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
        Fallback attribute lookup: if an attribute is not found on the SkillData instance,
        attempt to retrieve it from the extra data.
        """
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(
            f"'SkillData' object has no attribute '{attribute}'")


class SkillDataFilter:
    """
    Filter for /skilldata entries.

    Supported filterable attributes:
      - id: (str) Exact match.
      - name: (StringFilterType) Matching on the skill's name (exact, fuzzy using Fuzzy, or membership using IN).
      - imageURL: (StringFilterType) Filtering on the skill's image URL.
      - category: (StringFilterType) Filtering based on extra.category.
      - description: (StringFilterType) Filtering based on extra.description.
      - limit: (int) Maximum number of results returned.

    Examples:
        .. code-block:: python
        
            from dankmemer import SkillDataFilter, Fuzzy, IN

            filter_exact = SkillDataFilter(name="Haggler I")
            filter_fuzzy = SkillDataFilter(name=Fuzzy("haggler", cutoff=80))
            filter_category = SkillDataFilter(category=IN("Economy"))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        category: StringFilterType = None,
        description: StringFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.category: StringFilterType = category
        self.description: StringFilterType = description
        self.limit: IntegerType = limit

    def apply(self, data: List[SkillData]) -> List[SkillData]:
        results: List[SkillData] = []
        for skill in data:
            if self.id is not None and skill.id != self.id:
                continue
            if self.name is not None and not self._matches_field(skill.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(skill.imageURL, self.imageURL):
                continue

            skill_category = skill.extra.get("category", "")
            if self.category is not None and not self._matches_field(skill_category, self.category):
                continue

            skill_description = skill.extra.get("description", "")
            if self.description is not None and not self._matches_field(skill_description, self.description):
                continue

            results.append(skill)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)


class SkillDataRoute(CachedRoute[Dict[str, SkillData]]):
    """
    Represents the /skilldata endpoint, converting raw API data into SkillData objects
    and providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, SkillData]:
        raw_data: Any = await self.client.request("skillsdata")
        processed: Dict[str, SkillData] = {}
        if isinstance(raw_data, list):
            for entry in raw_data:
                skill = SkillData.from_dict(entry)
                processed[skill.id] = skill
        else:
            for key, value in raw_data.items():
                processed[key] = SkillData.from_dict(value)
        return self._store_cache(processed)

    async def query(self, skill_filter: Optional[SkillDataFilter] = None) -> List[SkillData]:
        """
        Retrieves the list of SkillData objects from the /skilldata endpoint.
        If a SkillDataFilter is provided, only those skills matching the filter criteria are returned.

        :param skill_filter: Optional SkillDataFilter for filtering criteria.
        :return: A list of SkillData objects.
        """
        raw_dict: Dict[str, SkillData] = await self._get_data()
        skill_list: List[SkillData] = list(raw_dict.values())
        if skill_filter is not None:
            skill_list = skill_filter.apply(skill_list)
        return skill_list

    async def iter_query(self, skill_filter: Optional[SkillDataFilter] = None) -> AsyncIterator[SkillData]:
        """
        Asynchronously iterates over SkillData objects from the /skilldata endpoint.
        If a SkillDataFilter is provided, only skills matching the criteria are iterated.

        :param skill_filter: Optional SkillDataFilter for filtering criteria.
        :return: An async iterator of SkillData objects.
        """
        raw_dict: Dict[str, SkillData] = await self._get_data()
        skill_list: List[SkillData] = list(raw_dict.values())
        if skill_filter is not None:
            skill_list = skill_filter.apply(skill_list)
        for skill in skill_list:
            yield skill
