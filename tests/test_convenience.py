from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import cast
from unittest.mock import patch as mock_patch

import pytest
from resource_helpers import (
    ManualClock,
    Responses,
    blog,
    blogs,
    first_record,
    item,
    page,
    skin,
)

from dankmemer import (
    AmbiguousLookupError,
    ConfigurationError,
    DankMemerResponseError,
    FishingCacheConfig,
    FishingCreature,
    FishingSkillRequirement,
    FishingSkillRequirements,
    FishingTime,
    LotteryResult,
    NotFound,
    TTLCache,
)
from dankmemer._parsing import Record
from dankmemer.models.fishing import parse_creature
from dankmemer.resources import (
    Blogs,
    Changelogs,
    Commands,
    Fishing,
    GlobalBoosts,
    Items,
    Lottery,
    MerchantTrades,
    Pets,
    Skins,
    Stream,
)


def cached() -> TTLCache:
    return TTLCache(ttl=timedelta(hours=1))


def pet(record_id: str, name: str) -> dict[str, object]:
    return {
        "id": record_id,
        "name": name,
        "imageUrl": "image",
        "cost": 100,
        "stats": {"hunger": 10, "hygiene": 10, "energy": 10, "fun": 10},
        "phrases": [],
        "words": [],
        "color": "blue",
        "friendlyTo": [],
        "hostileTo": [],
        "convertingPossible": True,
        "purchasable": True,
    }


def skill(tier: int) -> dict[str, object]:
    return {
        "id": f"skill-{tier}",
        "skillId": "skill",
        "tier": tier,
        "name": "Skill",
        "imageUrl": "image",
        "description": "Description",
        "category": "Experience",
        "requirements": {
            "badge": None,
            "skill": None if tier == 1 else {"id": "skill", "tier": tier - 1},
        },
    }


def merchant(*rewards: Mapping[str, object]) -> dict[str, object]:
    return {
        "data": {
            "date": "2026-10-01T00:00:00Z",
            "generatedAt": "2026-09-27T00:00:00Z",
            "trades": [
                {
                    "position": str(position),
                    "max": 3,
                    "reward": reward,
                    "forReward": {"type": "item", "item": 12, "quantity": 3},
                }
                for position, reward in enumerate(rewards)
            ],
        }
    }


@pytest.mark.asyncio
async def test_exact_name_key_id_search_and_fuzzy_search_have_distinct_semantics() -> (
    None
):
    records = [
        item(1, "Gold Bank", key="gold"),
        item(2, "Bank Note", key="note"),
        item(3, "Bank", key="bank"),
        item(4, "Banana", key="banana"),
    ]
    responses = Responses(page(*records))
    resource = Items(responses, cache=cached())
    assert await resource.get("bank not") is None
    exact = await resource.get("  bANK nOTE  ")
    assert exact is not None and exact.id == 2
    assert await resource.get_by_key("note") is exact
    assert await resource.get_by_key("NOTE") is None
    assert await resource.get_by_id(2) is exact
    assert [entry.name for entry in await resource.search("bank")] == [
        "Bank",
        "Bank Note",
        "Gold Bank",
    ]
    assert (
        await resource.search("bank not", fuzzy=True, min_score=90, max_limit=1)
    ) == (exact,)
    assert await resource.search("bank not", fuzzy=True, min_score=100) == ()
    assert await resource.get("Missing") is None
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_duplicate_names_are_reported_with_candidate_ids() -> None:
    responses = Responses(
        page(item(1, "Bank Note", key="one"), item(2, "bank note", key="two"))
    )
    resource = Items(responses, cache=cached())
    with pytest.raises(AmbiguousLookupError) as caught:
        await resource.get("Bank Note")
    assert caught.value.name == "Bank Note"
    assert caught.value.path == "/items"
    assert caught.value.candidate_ids == (1, 2)
    assert (await resource.get_by_id(2)) is not None
    assert len(await resource.search("bank note")) == 2
    assert len(responses.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("get", (" ",), {}),
        ("get", (1,), {}),
        ("get_by_id", ("12",), {}),
        ("get_by_id", (True,), {}),
        ("get_by_id", (-1,), {}),
        ("get_by_key", ("",), {}),
        ("search", ("bank",), {"max_limit": True}),
        ("search", ("bank",), {"max_limit": -1}),
        ("search", ("bank",), {"fuzzy": 1}),
        ("search", ("bank",), {"min_score": float("nan")}),
        ("search", ("bank",), {"min_score": 101}),
        ("search", ("bank",), {"min_score": 10**1000}),
        ("filter", (), {"min_value": 10, "max_value": 1}),
        ("filter", (), {"tags": ["test"]}),
        ("filter", (), {"min_market_value": True}),
        ("fetch", (), {"refresh": 1}),
    ],
)
async def test_invalid_catalog_queries_fail_before_requesting(
    method: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    responses = Responses()
    resource = Items(responses)
    operation = cast(Callable[..., Awaitable[object]], getattr(resource, method))
    with pytest.raises(ConfigurationError):
        await operation(*args, **kwargs)
    assert responses.calls == []


@pytest.mark.asyncio
async def test_zero_result_limits_and_empty_bulk_lookups_make_no_requests() -> None:
    responses = Responses()
    resource = Items(responses)
    assert await resource.search("bank", max_limit=0) == ()
    assert await resource.filter(max_limit=0) == ()
    assert await resource.get_many_by_id() == ()
    assert responses.calls == []


@pytest.mark.asyncio
async def test_item_filter_combines_tags_types_and_inclusive_value_bounds() -> None:
    match = item(1, "Bank Note")
    match["tags"] = ["Rare", "Useful"]
    other = item(2, "Not the same")
    other["marketValue"] = 121
    responses = Responses(page(match, other))
    resource = Items(responses, cache=cached())
    selected = await resource.filter(
        name="bank NOTE",
        type="Collectable",
        tags=("rare", "useful"),
        min_value=100,
        max_value=100,
        min_sell_value=20,
        max_sell_value=20,
        min_market_value=120,
        max_market_value=120,
    )
    assert [entry.id for entry in selected] == [1]
    assert await resource.filter(tags=("rare", "missing")) == ()
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_pet_filters_and_relationships_share_a_complete_catalog() -> None:
    cat, dog, bird = pet("cat", "Cat"), pet("dog", "Dog"), pet("bird", "Bird")
    cat["friendlyTo"], cat["hostileTo"] = ["dog", "bird"], ["bird"]
    dog["purchasable"] = False
    responses = Responses(page(cat, dog, bird))
    resource = Pets(responses, cache=cached())
    assert [entry.id for entry in await resource.friendly_to("CAT")] == ["dog", "bird"]
    assert [entry.id for entry in await resource.hostile_to("cat")] == ["bird"]
    assert [
        entry.id
        for entry in await resource.filter(
            min_cost=100, max_cost=100, purchasable=False, converting_possible=True
        )
    ] == ["dog"]
    assert await resource.friendly_to("Missing") == ()
    found = await resource.get("Cat")
    assert found is not None
    with pytest.raises(DankMemerResponseError) as caught:
        await resource.friendly_to(replace(found, friendly_to=("absent",)))
    assert caught.value.field == "friendlyTo"
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_skin_scope_disambiguates_names_and_handles_null_references() -> None:
    item_skin, pet_skin, unattached = skin(12), skin("cat"), skin(None)
    pet_skin.update(id="pet-skin", type="pet")
    unattached.update(id="unattached", key="unattached", name="Unattached")
    responses = Responses(page(item_skin, pet_skin, unattached))
    resource = Skins(responses, cache=cached())
    with pytest.raises(AmbiguousLookupError):
        await resource.get("Test skin")
    found = await resource.get("Test skin", type="ITEM", reference=12)
    assert found is not None and found.id == "test-skin"
    assert await resource.for_item(12) == (found,)
    assert [entry.id for entry in await resource.filter(has_reference=False)] == [
        "unattached"
    ]
    assert len(await resource.filter(reference=None)) == 3
    with pytest.raises(AmbiguousLookupError):
        await resource.get_by_key("test")
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_command_filters_and_nested_option_lookup_are_typed_and_local() -> None:
    argument = {"name": "value", "description": "Value", "type": 3, "required": False}
    subcommand = {
        "name": "show",
        "description": "Show",
        "type": 1,
        "options": [argument],
    }
    data = {
        "id": "test",
        "name": "Test",
        "description": "Description",
        "permissions": "none",
        "category": "Utility",
        "hybrid": True,
        "dms": False,
        "cooldown": 10,
        "donorCooldown": 5,
        "options": [subcommand],
    }
    responses = Responses(page(data))
    resource = Commands(responses, cache=cached())
    command = await resource.get("TEST")
    assert command is not None
    assert command.get_option(" SHOW ", "VALUE") == command.options[0].options[0]
    assert command.get_option("show", "missing") is None
    with pytest.raises(ConfigurationError):
        command.get_option()
    with pytest.raises(ConfigurationError):
        command.get_option("show", " ")
    assert await resource.filter(category="utility", hybrid=True, dms=False) == (
        command,
    )
    assert await resource.filter(dms=True) == ()
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_fishing_relationships_resolve_through_only_the_needed_categories() -> (
    None
):
    location = {
        "id": "river",
        "name": "River",
        "imageUrl": "image",
        "creatures": ["fish"],
        "npcs": ["npc"],
        "days": [],
        "disabled": False,
        "type": "main",
    }
    creature = {
        "id": "fish",
        "name": "Fish",
        "imageUrl": "image",
        "flavor": "Flavour",
        "boss": False,
        "mythical": True,
        "rarity": "Rare",
        "time": {"start": 0, "end": 24},
        "tools": {"rod": {"min": 1, "max": 2}},
        "variants": [],
        "locations": ["river"],
    }
    tool = {
        "id": "rod",
        "name": "Rod",
        "imageUrl": "image",
        "flavor": "Flavour",
        "usage": 100,
        "baits": True,
    }
    npc = {
        "id": "npc",
        "name": "NPC",
        "imageUrl": "image",
        "nickname": "Friend",
        "bio": "Biography",
    }
    responses = Responses(
        page(location, category="locations"),
        page(creature, category="creatures"),
        page(tool, category="tools"),
        page(npc, category="npcs"),
    )
    fishing = Fishing(
        responses,
        cache=FishingCacheConfig(
            locations=cached(), creatures=cached(), tools=cached(), npcs=cached()
        ),
    )
    found = (await fishing.creatures_at("river"))[0]
    places = await fishing.resolve_locations(found)
    assert places[0].id == "river"
    assert (await fishing.resolve_tools(found))[0].id == "rod"
    assert (await fishing.resolve_npcs(places[0]))[0].id == "npc"
    assert (await fishing.npcs.get_by_nickname(" FRIEND ")) is not None
    assert await fishing.creatures.filter(
        rarity="rare", boss=False, mythical=True, location_id="river", tool_id="rod"
    ) == (found,)
    assert await fishing.locations.filter(temporary=False) == ()
    assert len(await fishing.locations.filter(disabled=False, type="MAIN")) == 1
    assert await fishing.creatures_at("unknown") == ()
    assert [call.category for call in responses.calls] == [
        "locations",
        "creatures",
        "tools",
        "npcs",
    ]
    with pytest.raises(DankMemerResponseError) as caught:
        await fishing.resolve_tools(
            replace(found, tools={"absent": next(iter(found.tools.values()))})
        )
    assert caught.value.field == "tools"


@pytest.fixture
def fishing_creature() -> FishingCreature:
    return parse_creature(
        Record(first_record("/fish?category=creatures"), path="/fish")
    )


@pytest.mark.parametrize(
    ("start", "end", "reversed", "expected_start", "expected_end"),
    [
        (8, 20, None, "2026-12-31T08:00:00+00:00", "2026-12-31T20:00:00+00:00"),
        (8, 20, False, "2026-12-31T08:00:00+00:00", "2026-12-31T20:00:00+00:00"),
        (8, 20, True, "2026-12-31T20:00:00+00:00", "2027-01-01T08:00:00+00:00"),
        (20, 8, False, "2026-12-31T20:00:00+00:00", "2027-01-01T08:00:00+00:00"),
        (20, 8, True, "2026-12-31T08:00:00+00:00", "2026-12-31T20:00:00+00:00"),
        (0, 24, None, "2026-12-31T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        (0, 24, True, "2026-12-31T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        (8, 24, False, "2026-12-31T08:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        (8, 24, True, "2026-12-31T00:00:00+00:00", "2026-12-31T08:00:00+00:00"),
        (24, 8, False, "2026-12-31T00:00:00+00:00", "2026-12-31T08:00:00+00:00"),
        (8, 8, None, "2026-12-31T08:00:00+00:00", "2026-12-31T08:00:00+00:00"),
    ],
)
def test_fishing_availability_windows_preserve_legacy_time_conventions(
    fishing_creature: FishingCreature,
    start: int,
    end: int,
    reversed: bool | None,
    expected_start: str,
    expected_end: str,
) -> None:
    raw_time = FishingTime(start, end, reversed)
    creature = replace(fishing_creature, time=raw_time)
    starts_at, ends_at = creature.get_availability_window(day=date(2026, 12, 31))
    assert (starts_at, ends_at) == (
        datetime.fromisoformat(expected_start),
        datetime.fromisoformat(expected_end),
    )
    assert starts_at.tzinfo is UTC and ends_at.tzinfo is UTC
    assert creature.time is raw_time


def test_fishing_availability_defaults_to_the_current_utc_day(
    fishing_creature: FishingCreature,
) -> None:
    with mock_patch("dankmemer.models.fishing.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
        assert fishing_creature.get_availability_window() == (
            datetime(2026, 12, 31, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        )
        clock.now.assert_called_once_with(UTC)
        clock.now.return_value = datetime(2027, 1, 1, tzinfo=UTC)
        assert fishing_creature.get_availability_window() == (
            datetime(2027, 1, 1, tzinfo=UTC),
            datetime(2027, 1, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize(("start", "end"), [(-1, 24), (25, 0), (0, -1), (0, 25)])
def test_fishing_availability_rejects_bounds_outside_a_day(
    fishing_creature: FishingCreature, start: int, end: int
) -> None:
    creature = replace(fishing_creature, time=FishingTime(start, end, None))
    with pytest.raises(ValueError, match="between 0 and 24"):
        creature.get_availability_window(day=date(2026, 10, 3))


@pytest.mark.asyncio
async def test_fishing_bait_bucket_and_usage_filters() -> None:
    bait = {
        "id": "bait",
        "name": "Bait",
        "imageUrl": "image",
        "flavor": "Flavour",
        "usage": 10,
        "explanation": "Effect",
    }
    bucket = {
        "id": "bucket",
        "name": "Bucket",
        "imageUrl": "image",
        "flavor": "Flavour",
        "size": 100,
    }
    tool = {
        "id": "rod",
        "name": "Rod",
        "imageUrl": "image",
        "flavor": "Flavour",
        "usage": 100,
        "baits": True,
    }
    responses = Responses(
        page(bait, category="baits"),
        page(bucket, category="buckets"),
        page(tool, category="tools"),
    )
    fishing = Fishing(
        responses,
        cache=FishingCacheConfig(baits=cached(), buckets=cached(), tools=cached()),
    )
    assert len(await fishing.baits.filter(min_usage=10, max_usage=10)) == 1
    assert len(await fishing.buckets.filter(min_size=100, max_size=100)) == 1
    assert (
        len(await fishing.tools.filter(baits=True, min_usage=100, max_usage=100)) == 1
    )
    assert await fishing.tools.filter(baits=False) == ()
    assert [call.category for call in responses.calls] == ["baits", "buckets", "tools"]


@pytest.mark.asyncio
async def test_skill_name_ambiguity_tier_sorting_and_prerequisite_resolution() -> None:
    responses = Responses(page(skill(3), skill(1), skill(2), category="skills"))
    fishing = Fishing(responses, cache=FishingCacheConfig(skills=cached()))
    with pytest.raises(AmbiguousLookupError):
        await fishing.skills.get("Skill")
    found = await fishing.skills.get("SKILL", tier=2)
    assert found is not None and found.tier == 2
    prerequisite = await fishing.skills.prerequisite(found)
    assert prerequisite is not None and prerequisite.tier == 1
    assert await fishing.skills.prerequisite(prerequisite) is None
    assert [entry.tier for entry in await fishing.skills.tiers("skill")] == [1, 2, 3]
    assert await fishing.skills.filter(
        category="experience", min_tier=2, max_tier=2
    ) == (found,)
    missing = replace(
        found,
        requirements=FishingSkillRequirements(
            badge=None, skill=FishingSkillRequirement("absent", 1)
        ),
    )
    with pytest.raises(DankMemerResponseError, match="prerequisite"):
        await fishing.skills.prerequisite(missing)
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_publication_search_limits_scanning_independently_of_match_count() -> (
    None
):
    responses = Responses(
        blogs(*range(100), next_cursor=100), blogs(*range(100, 105), next_cursor=105)
    )
    resource = Blogs(responses)
    result = await resource.search("Blog 104", max_limit=1, scan_limit=105)
    assert [entry.id for entry in result] == ["104"]
    assert [(call.cursor, call.limit) for call in responses.calls] == [
        (0, 100),
        (100, 5),
    ]
    bounded = Responses(blogs(*range(100), next_cursor=100))
    assert await Blogs(bounded).search("Blog 104", max_limit=1) == ()
    assert len(bounded.calls) == 1


@pytest.mark.asyncio
async def test_publication_date_and_description_queries_reuse_only_identical_cached_pages() -> (
    None
):
    one, two = blog(1), blog(2)
    two["createdAt"] = "2026-10-02T01:00:00.125Z"
    responses = Responses(page(two, one), page(two))
    resource = Blogs(responses, cache=cached())
    assert [
        entry.id
        for entry in await resource.search("published", include_description=True)
    ] == ["1", "2"]
    assert await resource.search("published") == ()
    bound = datetime(2026, 10, 1, 2, 0, 0, 125000, tzinfo=timezone(timedelta(hours=1)))
    assert [entry.id for entry in await resource.filter(after=bound, before=bound)] == [
        "1"
    ]
    assert len(responses.calls) == 1
    latest = await resource.latest()
    assert latest is not None and latest.id == "2"
    assert len(responses.calls) == 2
    assert await resource.latest() is latest
    assert await resource.get_by_id("1") is not None
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_publication_id_search_stops_after_a_matching_page_and_documents_bounded_misses() -> (
    None
):
    responses = Responses(blogs(1, 2, next_cursor=2))
    resource = Blogs(responses, cache=cached())
    assert (await resource.get_by_id("1", scan_limit=2)) is not None
    assert await resource.get_by_id("3", scan_limit=2) is None
    assert len(responses.calls) == 1
    assert await resource.search("blog", max_limit=0, scan_limit=None) == ()
    assert await resource.get_by_id("3", scan_limit=0) is None
    assert await resource.filter(scan_limit=0) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"after": datetime(2026, 1, 1)},
        {"scan_limit": True},
        {"scan_limit": -1},
        {
            "after": datetime(2026, 2, 1, tzinfo=UTC),
            "before": datetime(2026, 1, 1, tzinfo=UTC),
        },
    ],
)
async def test_invalid_publication_bounds_do_not_make_requests(
    kwargs: dict[str, object],
) -> None:
    responses = Responses()
    operation = cast(Callable[..., Awaitable[object]], Blogs(responses).filter)
    with pytest.raises(ConfigurationError):
        await operation(**kwargs)
    assert responses.calls == []


@pytest.mark.asyncio
async def test_latest_publications_preserve_empty_data_and_http_errors() -> None:
    assert await Blogs(Responses(blogs())).latest() is None
    data = blog(1)
    changelog = await Changelogs(Responses(page(data))).latest()
    assert changelog is not None and changelog.id == "1"
    error = NotFound("missing", status_code=404, path="/blogs")
    with pytest.raises(NotFound):
        await Blogs(Responses(error)).latest()


@pytest.mark.asyncio
async def test_live_cache_can_retain_null_and_is_opt_in_with_forced_refresh() -> None:
    clock = ManualClock()
    responses = Responses(
        {"data": None},
        {
            "data": {
                "drawnAt": "2026-10-01T00:00:00Z",
                "winnings": 1000,
                "totalEntries": 100,
                "participants": 20,
                "winnerEntries": 10,
            }
        },
        {"data": None},
    )
    resource = Lottery(
        responses, cache=TTLCache(ttl=timedelta(seconds=60)), clock=clock
    )
    assert await resource.latest() is None
    assert await resource.latest() is None
    assert len(responses.calls) == 1
    drawn = await resource.latest(refresh=True)
    assert (
        drawn is not None
        and drawn.winner_entry_fraction == 0.1
        and drawn.entries_per_participant == 5
    )
    clock.elapsed = 60
    assert await resource.latest() is None
    assert len(responses.calls) == 3
    future = LotteryResult(datetime.now(UTC) + timedelta(days=1), 0, 0, 0, 0)
    assert future.age == timedelta(0)
    assert (
        future.winner_entry_fraction is None and future.entries_per_participant is None
    )


@pytest.mark.asyncio
async def test_boost_type_filters_share_a_single_live_cache_entry() -> None:
    data = [
        {"type": "xp", "multiplier": 2, "endsAt": "2000-01-01T00:00:00Z"},
        {"type": "coin", "multiplier": 3, "endsAt": "2000-01-01T00:00:00Z"},
    ]
    responses = Responses({"data": data})
    resource = GlobalBoosts(responses, cache=cached())
    assert [entry.type for entry in await resource.active(type="XP")] == ["xp"]
    assert len(await resource.active()) == 2
    assert await resource.active(type="absent") == ()
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_merchant_item_names_offer_filters_and_related_models_reuse_catalogs() -> (
    None
):
    rewards: list[dict[str, object]] = [
        {"type": "item", "item": 12, "quantity": 1},
        {"type": "fish-bait", "baitID": "bait", "quantity": 1},
        {"type": "future-reward"},
    ]
    item_responses = Responses(page(item(12, "Bank Note")))
    item_resource = Items(item_responses, cache=cached())
    bait = {
        "id": "bait",
        "name": "Bait",
        "imageUrl": "image",
        "flavor": "Flavour",
        "usage": 10,
        "explanation": "Effect",
    }
    bait_responses = Responses(page(bait, category="baits"))
    bait_resource = Fishing(
        bait_responses, cache=FishingCacheConfig(baits=cached())
    ).baits
    responses = Responses(merchant(*rewards))
    resource = MerchantTrades(
        responses, items=item_resource, baits=bait_resource, cache=cached()
    )
    assert await resource.for_item("Missing") == ()
    assert responses.calls == []
    trades = await resource.for_item("BANK NOTE")
    assert len(trades) == 3
    rotation = await resource.today()
    assert rotation is not None and rotation.trades_requiring(12) == trades
    assert rotation.trades_rewarding("ITEM") == (trades[0],)
    cost = await resource.resolve_cost(trades[0])
    assert cost is not None and cost.name == "Bank Note"
    assert await resource.resolve_reward(trades[0]) is cost
    resolved_bait = await resource.resolve_reward(trades[1])
    assert resolved_bait is not None and resolved_bait.name == "Bait"
    assert await resource.resolve_reward(trades[2]) is None
    assert (
        len(item_responses.calls)
        == len(bait_responses.calls)
        == len(responses.calls)
        == 1
    )
    with pytest.raises(DankMemerResponseError) as caught:
        await resource.resolve_reward(
            replace(trades[0], reward=replace(trades[0].reward, item_id=999))
        )
    assert caught.value.field == "reward.item"
    with pytest.raises(DankMemerResponseError):
        await resource.resolve_cost(
            replace(trades[0], cost=replace(trades[0].cost, item_id=None))
        )


@pytest.mark.asyncio
async def test_stream_lookup_uses_cache_and_propagates_unavailable_route() -> None:
    responses = Responses({"data": {"game": "Test game"}})
    resource = Stream(responses, cache=cached())
    assert await resource.trending_game() == "Test game"
    assert await resource.trending_game() == "Test game"
    assert len(responses.calls) == 1
    missing = NotFound("missing", status_code=404, path="/stream-trending-game")
    with pytest.raises(NotFound) as caught:
        await Stream(Responses(missing)).trending_game()
    assert caught.value is missing
