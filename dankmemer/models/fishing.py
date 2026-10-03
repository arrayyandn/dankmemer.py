from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from dankmemer._parsing import Record


@dataclass(frozen=True, slots=True)
class FishingTime:
    """A creature's fishing time window, using the API's numeric bounds.

    Values remain as supplied by the API. The creature's
    :meth:`~dankmemer.FishingCreature.get_availability_window` helper interprets
    them as UTC hours.

    Attributes
    ----------
    start : int
        Start bound as the API's integer value.
    end : int
        End bound as the API's integer value.
    reversed : bool | None
        API reversal flag, or None when omitted. The availability helper swaps
        the bounds when this is True.
    """

    start: int
    end: int
    reversed: bool | None


@dataclass(frozen=True, slots=True)
class FishingToolRange:
    """The minimum and maximum values published for a creature and tool.

    Attributes
    ----------
    min : int
        Minimum value published for this creature and tool.
    max : int
        Maximum value published for this creature and tool.
    """

    min: int
    max: int


@dataclass(frozen=True, slots=True)
class FishingVariant:
    """A variant of a fishing creature.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    type : str
        Type identifier supplied by the API.
    image_url : str
        URL of the record's image.
    """

    id: str
    name: str
    type: str
    image_url: str


@dataclass(frozen=True, slots=True)
class FishingCreature:
    """A fishing creature and its variants, locations, and tool ranges.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    flavor : str
        Flavor text published with the entry.
    image_url : str
        URL of the record's image.
    boss : bool
        Whether the catalog marks this creature as a boss.
    mythical : bool
        Whether the catalog marks this creature as mythical.
    rarity : str
        Rarity label supplied by the API.
    time : FishingTime
        Published fishing time bounds.
    tools : Mapping[str, FishingToolRange]
        Read-only mapping of tool IDs to their published ranges.
    variants : tuple[FishingVariant, ...]
        Creature variants included in the record.
    locations : tuple[str, ...]
        Location IDs; resolve them through dank.fishing.resolve_locations.
    """

    id: str
    name: str
    flavor: str
    image_url: str
    boss: bool
    mythical: bool
    rarity: str
    time: FishingTime
    tools: Mapping[str, FishingToolRange]
    variants: tuple[FishingVariant, ...]
    locations: tuple[str, ...]

    def get_availability_window(
        self, *, day: date | None = None
    ) -> tuple[datetime, datetime]:
        """Return the creature's availability window as aware UTC datetimes.

        Interpret the raw time bounds as hours from 0 to 24. The range 0 to 24
        covers the whole day. Otherwise, ``reversed=True`` swaps the bounds,
        and 24 represents midnight. If the start is later than the end, the
        window ends the following day. Equal bounds give a zero-length window.
        These conventions match the previous client's availability helper.

        This calculation uses the model's time data and makes no requests.
        Location schedules and other requirements still apply when fishing.

        Parameters
        ----------
        day : datetime.date | None
            Date on which the window starts. Defaults to today's UTC date.
            An overnight window stays anchored to this date, including when
            its start is still in the future.

        Returns
        -------
        tuple[datetime.datetime, datetime.datetime]
            Start and end of the window. The start is inclusive and the end
            is exclusive.

        Raises
        ------
        ValueError
            A time bound is outside the range 0 to 24.
        """
        start_hour, end_hour = self.time.start, self.time.end
        if not 0 <= start_hour <= 24 or not 0 <= end_hour <= 24:
            raise ValueError("fishing time bounds must be between 0 and 24 hours")
        if day is None:
            day = datetime.now(UTC).date()
        midnight = datetime.combine(day, time.min, tzinfo=UTC)

        # The 0-to-24 full-day range takes precedence over reversal, as in rc3.
        if start_hour == 0 and end_hour == 24:
            return midnight, midnight + timedelta(days=1)
        if self.time.reversed:
            start_hour, end_hour = end_hour, start_hour
        start = midnight + timedelta(hours=start_hour % 24)
        end = midnight + timedelta(hours=end_hour % 24)
        if start > end:
            end += timedelta(days=1)
        return start, end


@dataclass(frozen=True, slots=True)
class FishingLocation:
    """A fishing location's catalog entry.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    image_url : str
        URL of the record's image.
    creatures : tuple[str, ...]
        Creature IDs associated with the location.
    npcs : tuple[str, ...]
        NPC IDs associated with the location.
    days : tuple[int, ...]
        Numeric day identifiers supplied by the API; no weekday mapping is
        assumed.
    disabled : bool
        Whether the API marks this location as disabled.
    type : str
        Type identifier supplied by the API.
    temporary : bool | None
        Whether the location is temporary, or None when the field is omitted.
    """

    id: str
    name: str
    image_url: str
    creatures: tuple[str, ...]
    npcs: tuple[str, ...]
    days: tuple[int, ...]
    disabled: bool
    type: str
    temporary: bool | None


@dataclass(frozen=True, slots=True)
class FishingNPC:
    """A fishing NPC's name, biography, and image.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    nickname : str
        NPC nickname, accepted by get_by_nickname.
    bio : str
        Published biography.
    image_url : str
        URL of the record's image.
    """

    id: str
    name: str
    nickname: str
    bio: str
    image_url: str


@dataclass(frozen=True, slots=True)
class FishingTool:
    """A fishing tool's catalog entry.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    flavor : str
        Flavor text published with the entry.
    usage : int
        Published usage value, not a user's remaining uses.
    baits : bool
        Whether the tool supports bait.
    image_url : str
        URL of the record's image.
    """

    id: str
    name: str
    flavor: str
    usage: int
    baits: bool
    image_url: str


@dataclass(frozen=True, slots=True)
class FishingBait:
    """A bait's catalog entry, including its effect explanation.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    flavor : str
        Flavor text published with the entry.
    usage : int
        Published usage value, not a user's remaining uses.
    explanation : str
        Explanation of the bait's effect.
    image_url : str
        URL of the record's image.
    """

    id: str
    name: str
    flavor: str
    usage: int
    explanation: str
    image_url: str


@dataclass(frozen=True, slots=True)
class FishingBucket:
    """A fishing bucket's catalog entry and size.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    flavor : str
        Flavor text published with the entry.
    size : int
        Published bucket capacity.
    image_url : str
        URL of the record's image.
    """

    id: str
    name: str
    flavor: str
    size: int
    image_url: str


@dataclass(frozen=True, slots=True)
class FishingBadgeRequirement:
    """The badge required by a fishing skill.

    Attributes
    ----------
    id : str
        Identifier of the required badge.
    name : str
        Display name.
    image_url : str
        URL of the record's image.
    platinum : bool
        Whether the requirement specifies a platinum badge.
    """

    id: str
    name: str
    image_url: str
    platinum: bool


@dataclass(frozen=True, slots=True)
class FishingSkillRequirement:
    """The ID and tier of a prerequisite fishing skill.

    Attributes
    ----------
    id : str
        Skill identifier used to find the prerequisite tier.
    tier : int
        Required skill tier.
    """

    id: str
    tier: int


@dataclass(frozen=True, slots=True)
class FishingSkillRequirements:
    """A skill's badge and skill prerequisites.

    Attributes
    ----------
    badge : FishingBadgeRequirement | None
        Required badge, or None when there is no badge prerequisite.
    skill : FishingSkillRequirement | None
        Required skill and tier, or None when there is no skill prerequisite.
    """

    badge: FishingBadgeRequirement | None
    skill: FishingSkillRequirement | None


@dataclass(frozen=True, slots=True)
class FishingSkill:
    """One tier of a fishing skill.

    Attributes
    ----------
    id : str
        Identifier of this particular skill-tier record.
    skill_id : str
        Skill identifier shared across its tiers.
    tier : int
        Tier of this skill record.
    name : str
        Display name.
    image_url : str
        URL of the record's image.
    description : str
        Published description.
    requirements : FishingSkillRequirements
        Badge and skill prerequisites.
    category : str
        Category identifier supplied by the API.
    """

    id: str
    skill_id: str
    tier: int
    name: str
    image_url: str
    description: str
    requirements: FishingSkillRequirements
    category: str


def parse_creature(record: Record) -> FishingCreature:
    time = record.record("time")
    return FishingCreature(
        id=record.string("id"),
        name=record.string("name"),
        flavor=record.string("flavor"),
        image_url=record.string("imageUrl"),
        boss=record.boolean("boss"),
        mythical=record.boolean("mythical"),
        rarity=record.string("rarity"),
        time=FishingTime(
            start=time.integer("start"),
            end=time.integer("end"),
            reversed=time.optional_boolean("reversed"),
        ),
        tools=record.mapping("tools", parse_tool_range),
        variants=tuple(
            parse_variant(variant) for variant in record.records("variants")
        ),
        locations=record.strings("locations"),
    )


def parse_tool_range(record: Record) -> FishingToolRange:
    return FishingToolRange(min=record.integer("min"), max=record.integer("max"))


def parse_variant(record: Record) -> FishingVariant:
    return FishingVariant(
        id=record.string("id"),
        name=record.string("name"),
        type=record.string("type"),
        image_url=record.string("imageUrl"),
    )


def parse_location(record: Record) -> FishingLocation:
    return FishingLocation(
        id=record.string("id"),
        name=record.string("name"),
        image_url=record.string("imageUrl"),
        creatures=record.strings("creatures"),
        npcs=record.strings("npcs"),
        days=record.integers("days"),
        disabled=record.boolean("disabled"),
        type=record.string("type"),
        temporary=record.optional_boolean("temporary"),
    )


def parse_npc(record: Record) -> FishingNPC:
    return FishingNPC(
        id=record.string("id"),
        name=record.string("name"),
        nickname=record.string("nickname"),
        bio=record.string("bio"),
        image_url=record.string("imageUrl"),
    )


def parse_tool(record: Record) -> FishingTool:
    return FishingTool(
        id=record.string("id"),
        name=record.string("name"),
        flavor=record.string("flavor"),
        usage=record.integer("usage"),
        baits=record.boolean("baits"),
        image_url=record.string("imageUrl"),
    )


def parse_bait(record: Record) -> FishingBait:
    return FishingBait(
        id=record.string("id"),
        name=record.string("name"),
        flavor=record.string("flavor"),
        usage=record.integer("usage"),
        explanation=record.string("explanation"),
        image_url=record.string("imageUrl"),
    )


def parse_bucket(record: Record) -> FishingBucket:
    return FishingBucket(
        id=record.string("id"),
        name=record.string("name"),
        flavor=record.string("flavor"),
        size=record.integer("size"),
        image_url=record.string("imageUrl"),
    )


def parse_skill(record: Record) -> FishingSkill:
    requirements = record.record("requirements")
    badge = requirements.nullable_record("badge")
    skill = requirements.nullable_record("skill")
    return FishingSkill(
        id=record.string("id"),
        skill_id=record.string("skillId"),
        tier=record.integer("tier"),
        name=record.string("name"),
        image_url=record.string("imageUrl"),
        description=record.string("description"),
        requirements=FishingSkillRequirements(
            badge=None
            if badge is None
            else FishingBadgeRequirement(
                id=badge.string("id"),
                name=badge.string("name"),
                image_url=badge.string("imageUrl"),
                platinum=badge.boolean("platinum"),
            ),
            skill=None
            if skill is None
            else FishingSkillRequirement(
                id=skill.string("id"), tier=skill.integer("tier")
            ),
        ),
        category=record.string("category"),
    )
