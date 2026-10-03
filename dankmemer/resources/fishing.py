from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from dankmemer._clock import Clock
from dankmemer.config import CachePolicy, FishingCacheConfig
from dankmemer.errors import DankMemerResponseError
from dankmemer.http._routes import FISH
from dankmemer.models.fishing import (
    FishingBait,
    FishingBucket,
    FishingCreature,
    FishingLocation,
    FishingNPC,
    FishingSkill,
    FishingTool,
    parse_bait,
    parse_bucket,
    parse_creature,
    parse_location,
    parse_npc,
    parse_skill,
    parse_tool,
)

from ._base import Requester, Resource
from ._catalog import CatalogResource
from ._queries import (
    boolean,
    integer,
    integer_range,
    model,
    normalise,
    optional_boolean,
    optional_text,
    text,
    within,
)

_T = TypeVar("_T")


class Creatures(CatalogResource[FishingCreature, str]):
    """Fetch or iterate over fishing creatures and their variants."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_creature,
            str,
            category="creatures",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        rarity: str | None = None,
        boss: bool | None = None,
        mythical: bool | None = None,
        location_id: str | None = None,
        tool_id: str | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingCreature, ...]:
        """Filter catalog creatures by rarity, flags, location ID, or tool ID.

        All supplied conditions must match. A matching catalog entry does
        not imply that a creature is available at the current time.
        """
        rarity = optional_text(rarity, "rarity")
        optional_boolean(boss, "boss")
        optional_boolean(mythical, "mythical")
        location_id = None if location_id is None else text(location_id, "location_id")
        tool_id = None if tool_id is None else text(tool_id, "tool_id")
        return await self._select(
            lambda creature: (
                (rarity is None or normalise(creature.rarity) == rarity)
                and (boss is None or creature.boss is boss)
                and (mythical is None or creature.mythical is mythical)
                and (location_id is None or location_id in creature.locations)
                and (tool_id is None or tool_id in creature.tools)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )


class Locations(CatalogResource[FishingLocation, str]):
    """Fetch or iterate over fishing locations, including disabled entries."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_location,
            str,
            category="locations",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        type: str | None = None,
        disabled: bool | None = None,
        temporary: bool | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingLocation, ...]:
        """Filter location types or explicit disabled/temporary flags.

        ``temporary=False`` selects explicit false values, excluding entries
        where the API omits that flag. This does not evaluate day schedules.
        """
        location_type = optional_text(type, "type")
        optional_boolean(disabled, "disabled")
        optional_boolean(temporary, "temporary")
        return await self._select(
            lambda location: (
                (location_type is None or normalise(location.type) == location_type)
                and (disabled is None or location.disabled is disabled)
                and (temporary is None or location.temporary is temporary)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )


class NPCs(CatalogResource[FishingNPC, str]):
    """Fetch or iterate over fishing NPCs."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_npc,
            str,
            category="npcs",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def get_by_nickname(
        self, nickname: str, *, refresh: bool = False
    ) -> FishingNPC | None:
        """Look up an exact nickname, ignoring case and surrounding whitespace."""
        nickname = text(nickname, "nickname")
        matches = await self._select(
            lambda npc: normalise(npc.nickname) == normalise(nickname),
            max_limit=None,
            refresh=refresh,
        )
        return self._unique(matches, nickname)


class Tools(CatalogResource[FishingTool, str]):
    """Fetch or iterate over fishing tools."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_tool,
            str,
            category="tools",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        baits: bool | None = None,
        min_usage: int | None = None,
        max_usage: int | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingTool, ...]:
        """Filter tools by bait support and inclusive catalog usage bounds."""
        optional_boolean(baits, "baits")
        integer_range(min_usage, max_usage, "usage")
        return await self._select(
            lambda tool: (
                (baits is None or tool.baits is baits)
                and within(tool.usage, min_usage, max_usage)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )


class Baits(CatalogResource[FishingBait, str]):
    """Fetch or iterate over fishing baits."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_bait,
            str,
            category="baits",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        min_usage: int | None = None,
        max_usage: int | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingBait, ...]:
        """Filter baits by inclusive catalog usage bounds."""
        integer_range(min_usage, max_usage, "usage")
        return await self._select(
            lambda bait: within(bait.usage, min_usage, max_usage),
            max_limit=max_limit,
            refresh=refresh,
        )


class Buckets(CatalogResource[FishingBucket, str]):
    """Fetch or iterate over fishing buckets."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_bucket,
            str,
            category="buckets",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingBucket, ...]:
        """Filter buckets by inclusive size bounds."""
        integer_range(min_size, max_size, "size")
        return await self._select(
            lambda bucket: within(bucket.size, min_size, max_size),
            max_limit=max_limit,
            refresh=refresh,
        )


class Skills(CatalogResource[FishingSkill, str]):
    """Fetch or iterate over fishing skill tiers and prerequisites."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            FISH,
            parse_skill,
            str,
            category="skills",
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def get(
        self, name: str, *, tier: int | None = None, refresh: bool = False
    ) -> FishingSkill | None:
        """Look up a skill name, optionally choosing a particular tier."""
        name = text(name, "name")
        if tier is not None:
            integer(tier, "tier")
        catalog = await self._catalog(refresh=refresh)
        return self._unique(
            tuple(
                skill
                for skill in catalog.names.get(normalise(name), ())
                if tier is None or skill.tier == tier
            ),
            name,
        )

    async def filter(
        self,
        *,
        category: str | None = None,
        skill_id: str | None = None,
        min_tier: int | None = None,
        max_tier: int | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[FishingSkill, ...]:
        """Filter skill categories, group IDs, or inclusive tier bounds."""
        category = optional_text(category, "category")
        skill_id = None if skill_id is None else text(skill_id, "skill_id")
        integer_range(min_tier, max_tier, "tier")
        return await self._select(
            lambda skill: (
                (category is None or normalise(skill.category) == category)
                and (skill_id is None or skill.skill_id == skill_id)
                and within(skill.tier, min_tier, max_tier)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )

    async def tiers(
        self, skill_id: str, *, refresh: bool = False
    ) -> tuple[FishingSkill, ...]:
        """Return every tier for a skill group ID, ordered by tier."""
        skill_id = text(skill_id, "skill_id")
        matches = await self.filter(skill_id=skill_id, max_limit=None, refresh=refresh)
        return tuple(sorted(matches, key=lambda skill: skill.tier))

    async def prerequisite(
        self, skill: FishingSkill, *, refresh: bool = False
    ) -> FishingSkill | None:
        """Resolve the prerequisite skill, or return ``None`` when none is required.

        A prerequisite missing from the catalog raises a response error.
        """
        skill = model(skill, FishingSkill, "skill")
        boolean(refresh, "refresh")
        self._check_access()
        requirement = skill.requirements.skill
        if requirement is None:
            return None
        matches = await self.filter(
            skill_id=requirement.id,
            min_tier=requirement.tier,
            max_tier=requirement.tier,
            max_limit=None,
            refresh=refresh,
        )
        result = self._unique(matches, requirement.id)
        if result is None:
            raise DankMemerResponseError(
                "prerequisite skill is absent from the catalog",
                path=FISH.template,
                field="requirements.skill",
            )
        return result


class Fishing:
    """Typed resources for each of the official fishing categories.

    Each attribute provides lookup, search, ``fetch``, and ``iter`` methods. Requests select
    only that category; accessing an attribute does not fetch any data.
    """

    def __init__(
        self,
        request: Requester,
        *,
        cache: FishingCacheConfig | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        cache = cache if cache is not None else FishingCacheConfig()
        self.creatures = Creatures(
            request, cache=cache.creatures, clock=clock, guard=guard
        )
        self.locations = Locations(
            request, cache=cache.locations, clock=clock, guard=guard
        )
        self.npcs = NPCs(request, cache=cache.npcs, clock=clock, guard=guard)
        self.tools = Tools(request, cache=cache.tools, clock=clock, guard=guard)
        self.baits = Baits(request, cache=cache.baits, clock=clock, guard=guard)
        self.buckets = Buckets(request, cache=cache.buckets, clock=clock, guard=guard)
        self.skills = Skills(request, cache=cache.skills, clock=clock, guard=guard)
        self._resources: tuple[Resource, ...] = (
            self.creatures,
            self.locations,
            self.npcs,
            self.tools,
            self.baits,
            self.buckets,
            self.skills,
        )

    def clear_cache(self) -> None:
        """Discard cached data for every fishing category."""
        for resource in self._resources:
            resource.clear_cache()

    async def creatures_at(
        self, location: FishingLocation | str, *, refresh: bool = False
    ) -> tuple[FishingCreature, ...]:
        """Resolve a location model or exact name to its catalog creatures.

        An unknown location name returns an empty tuple. This does not
        evaluate whether that location or its creatures are available now.
        """
        boolean(refresh, "refresh")
        if isinstance(location, str):
            found = await self.locations.get(location, refresh=refresh)
            if found is None:
                return ()
            location = found
        location = model(location, FishingLocation, "location")
        return require_related(
            await self.creatures.get_many_by_id(*location.creatures, refresh=refresh),
            "creatures",
        )

    async def resolve_locations(
        self, creature: FishingCreature, *, refresh: bool = False
    ) -> tuple[FishingLocation, ...]:
        """Resolve a creature's location IDs in one location-catalog load."""
        creature = model(creature, FishingCreature, "creature")
        return require_related(
            await self.locations.get_many_by_id(*creature.locations, refresh=refresh),
            "locations",
        )

    async def resolve_tools(
        self, creature: FishingCreature, *, refresh: bool = False
    ) -> tuple[FishingTool, ...]:
        """Resolve a creature's tool IDs in one tool-catalog load."""
        creature = model(creature, FishingCreature, "creature")
        return require_related(
            await self.tools.get_many_by_id(*creature.tools, refresh=refresh), "tools"
        )

    async def resolve_npcs(
        self, location: FishingLocation, *, refresh: bool = False
    ) -> tuple[FishingNPC, ...]:
        """Resolve a location's NPC IDs in one NPC-catalog load."""
        location = model(location, FishingLocation, "location")
        return require_related(
            await self.npcs.get_many_by_id(*location.npcs, refresh=refresh), "npcs"
        )


def require_related(values: tuple[_T | None, ...], field: str) -> tuple[_T, ...]:
    results: list[_T] = []
    for value in values:
        if value is None:
            raise DankMemerResponseError(
                "related record is absent from the catalog",
                path=FISH.template,
                field=field,
            )
        results.append(value)
    return tuple(results)
