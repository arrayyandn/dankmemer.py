import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import assert_type, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from resource_helpers import Call, Responses, blog, blogs, item, skin

import dankmemer
from dankmemer import (
    Blog,
    Changelog,
    Command,
    ConfigurationError,
    DankMemer,
    DankMemerResponseError,
    FishingBait,
    FishingBucket,
    FishingCreature,
    FishingLocation,
    FishingNPC,
    FishingSkill,
    FishingTool,
    GlobalBoost,
    Item,
    LotteryResult,
    MerchantRotation,
    Page,
    PaginationError,
    Pet,
    Skin,
)
from dankmemer.http._routes import BLOGS, FISH, USER_BAN_STATUS
from dankmemer.resources import (
    Baits,
    Blogs,
    Commands,
    Fishing,
    GlobalBoosts,
    Items,
    Lottery,
    MerchantTrades,
    Skins,
    Users,
)


@pytest.mark.asyncio
async def test_iterator_is_lazy_bounded_and_reduces_last_request() -> None:
    responses = Responses(
        blogs(5, 4, next_cursor=2),
        blogs(3, 2, next_cursor=4),
        blogs(1, next_cursor=5),
    )
    iterator = Blogs(responses).iter(page_size=2, max_limit=5)
    assert responses.calls == []
    first = await anext(iterator)
    assert first.id == "5"
    assert len(responses.calls) == 1
    assert [entry.id async for entry in iterator] == ["4", "3", "2", "1"]
    assert [(call.cursor, call.limit) for call in responses.calls] == [
        (0, 2),
        (2, 2),
        (4, 1),
    ]
    assert all(call.route is BLOGS and not call.automatic for call in responses.calls)


@pytest.mark.asyncio
async def test_iterator_defaults_to_100_and_zero_makes_no_requests() -> None:
    responses = Responses(blogs(*range(100), next_cursor=100))
    resource = Blogs(responses)
    assert len([entry async for entry in resource.iter()]) == 100
    assert len(responses.calls) == 1
    assert [entry async for entry in resource.iter(max_limit=0)] == []
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_iterator_deduplicates_overlap_and_can_be_explicitly_unbounded() -> None:
    responses = Responses(
        blogs(3, 2, next_cursor=2),
        blogs(2, 1, next_cursor=4),
        blogs(0),
    )
    entries = [
        entry async for entry in Blogs(responses).iter(page_size=2, max_limit=None)
    ]
    assert [entry.id for entry in entries] == ["3", "2", "1", "0"]
    assert len(responses.calls) == 3


@pytest.mark.asyncio
async def test_iterator_stops_a_non_progressing_collection() -> None:
    responses = Responses(blogs(2, 1, next_cursor=2), blogs(2, 1, next_cursor=4))
    iterator = Blogs(responses).iter(page_size=2)
    assert (await anext(iterator)).id == "2"
    assert (await anext(iterator)).id == "1"
    with pytest.raises(PaginationError, match="no new record IDs"):
        await anext(iterator)
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_explicit_page_keeps_order_and_offsets_without_fetching_more() -> None:
    responses = Responses(blogs(7, 6, next_cursor=27), {"data": [], "nextCursor": None})
    resource = Blogs(responses)
    page = await resource.fetch(page_size=2, cursor=25)
    assert [entry.id for entry in page.items] == ["7", "6"]
    assert page.has_more and page.next_cursor == 27
    assert len(responses.calls) == 1
    final = await resource.fetch(cursor=27)
    assert final.items == () and not final.has_more


@pytest.mark.asyncio
@pytest.mark.parametrize("next_cursor", [True, False, "2", 0, -1, 2.5])
async def test_invalid_next_cursor_is_a_pagination_error(next_cursor: object) -> None:
    resource = Blogs(Responses(blogs(1, next_cursor=next_cursor)))
    with pytest.raises(PaginationError) as caught:
        await resource.fetch()
    assert caught.value.path == "/blogs"
    assert caught.value.field == "nextCursor"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "page_size"),
    [
        (blogs(1, 1), 100),
        (blogs(2, 1), 1),
        (blogs(next_cursor=100), 100),
    ],
)
async def test_malformed_pages_do_not_silently_truncate(
    payload: object, page_size: int
) -> None:
    with pytest.raises(PaginationError):
        await Blogs(Responses(payload)).fetch(page_size=page_size)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": True},
        {"page_size": 0},
        {"page_size": 101},
        {"cursor": -1},
        {"cursor": True},
    ],
)
async def test_invalid_pagination_arguments_make_no_requests(
    kwargs: dict[str, object],
) -> None:
    responses = Responses()
    resource = Blogs(responses)
    fetch = cast(Callable[..., Awaitable[object]], resource.fetch)
    iterate = cast(Callable[..., AsyncIterator[object]], resource.iter)
    with pytest.raises(ConfigurationError):
        await fetch(**kwargs)
    with pytest.raises(ConfigurationError):
        iterate(**kwargs)
    assert responses.calls == []


@pytest.mark.parametrize("max_limit", [True, False, -1, 1.5, "100"])
def test_invalid_total_limit_fails_before_iteration(max_limit: object) -> None:
    responses = Responses()
    iterate = cast(Callable[..., AsyncIterator[object]], Blogs(responses).iter)
    with pytest.raises(ConfigurationError):
        iterate(max_limit=max_limit)
    assert responses.calls == []


@pytest.mark.asyncio
async def test_catalog_models_convert_nested_fields_and_ignore_additions() -> None:
    source = item()
    model = (
        await Items(Responses({"data": [source], "nextCursor": None})).fetch()
    ).items[0]
    assert model.id == 12 and model.sell_value == 20 and model.market_value == 120
    assert model.tags == ("test",)
    assert model.skins[0].reference == 12
    assert model.skins[0].metadata is not None
    theme = model.skins[0].metadata["theme"]
    assert isinstance(theme, Mapping) and theme["colours"] == ("blue", "green")
    with pytest.raises(FrozenInstanceError):
        setattr(model, "name", "Changed")  # noqa: B010 - test runtime immutability
    with pytest.raises(TypeError):
        cast(dict[str, object], model.skins[0].metadata)["theme"] = "Changed"
    source["skins"] = []
    assert len(model.skins) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [12, "cat", None])
async def test_skin_reference_retains_its_api_type(reference: int | str | None) -> None:
    data = skin(reference)
    data["metadata"] = None
    model = (
        await Skins(Responses({"data": [data], "nextCursor": None})).fetch()
    ).items[0]
    assert model.reference == reference and type(model.reference) is type(reference)
    assert model.metadata is None and model.rarity == "future-rarity"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "12", 12.5, None])
async def test_item_id_requires_an_actual_integer(value: object) -> None:
    data = item()
    data["id"] = value
    with pytest.raises(DankMemerResponseError) as caught:
        await Items(Responses({"data": [data], "nextCursor": None})).fetch()
    assert caught.value.field == "data[0].id"
    assert caught.value.path == "/items"


@pytest.mark.asyncio
async def test_missing_nested_field_reports_its_location() -> None:
    data = item()
    incomplete_skin = skin()
    del incomplete_skin["imageUrl"]
    data["skins"] = [incomplete_skin]
    with pytest.raises(DankMemerResponseError) as caught:
        await Items(Responses({"data": [data], "nextCursor": None})).fetch()
    assert caught.value.field == "data[0].skins[0].imageUrl"


@pytest.mark.asyncio
async def test_nested_command_options_preserve_optional_required_flags() -> None:
    argument = {
        "name": "target",
        "description": "A target",
        "type": 6,
        "required": False,
    }
    subcommand = {
        "name": "show",
        "description": "Show it",
        "type": 1,
        "options": [argument],
    }
    data = {
        "id": "test",
        "name": "test",
        "description": "A command",
        "permissions": "none",
        "category": "test",
        "hybrid": True,
        "dms": False,
        "cooldown": 1000,
        "donorCooldown": 500,
        "options": [subcommand],
    }
    model = (
        await Commands(Responses({"data": [data], "nextCursor": None})).fetch()
    ).items[0]
    assert model.cooldown == 1000 and model.donor_cooldown == 500
    assert model.options[0].required is None
    assert model.options[0].options[0].required is False
    assert model.options[0].options[0].options == ()


@pytest.mark.asyncio
async def test_dates_are_aware_utc_and_naive_dates_are_rejected() -> None:
    data = blog(1)
    data["createdAt"] = "2026-10-01T02:00:00.125+01:00"
    model = (
        await Blogs(Responses({"data": [data], "nextCursor": None})).fetch()
    ).items[0]
    assert model.created_at == datetime(2026, 10, 1, 1, 0, 0, 125000, tzinfo=UTC)
    assert model.created_at.tzinfo is UTC
    data["createdAt"] = "2026-10-01T01:00:00"
    with pytest.raises(DankMemerResponseError) as caught:
        await Blogs(Responses({"data": [data], "nextCursor": None})).fetch()
    assert caught.value.field == "data[0].createdAt"


@pytest.mark.asyncio
async def test_fishing_category_is_selected_and_optional_fields_stay_absent() -> None:
    data = {
        "id": "fish",
        "name": "Fish",
        "flavor": "Flavour",
        "imageUrl": "image",
        "boss": False,
        "mythical": False,
        "rarity": "common",
        "time": {"start": 0, "end": 24},
        "tools": {"fishing-rod": {"min": 1, "max": 2}},
        "variants": [],
        "locations": ["river"],
    }
    responses = Responses({"data": [data], "nextCursor": None, "category": "creatures"})
    model = (await Fishing(responses).creatures.fetch(page_size=25)).items[0]
    assert model.time.reversed is None
    assert model.tools["fishing-rod"].max == 2 and model.locations == ("river",)
    assert responses.calls == [Call(FISH, None, 0, 25, "creatures", False)]
    with pytest.raises(TypeError):
        cast(dict[str, object], model.tools)["fishing-rod"] = None


@pytest.mark.asyncio
async def test_fishing_rejects_a_response_for_another_category() -> None:
    responses = Responses({"data": [], "nextCursor": None, "category": "tools"})
    with pytest.raises(DankMemerResponseError) as caught:
        await Fishing(responses).baits.fetch()
    assert caught.value.field == "category"


@pytest.mark.asyncio
async def test_skill_prerequisites_have_models_and_explicit_absence() -> None:
    data = {
        "id": "skill-2",
        "skillId": "skill",
        "tier": 2,
        "name": "Skill",
        "imageUrl": "image",
        "description": "Description",
        "category": "test",
        "requirements": {
            "badge": {
                "id": "badge",
                "name": "Badge",
                "imageUrl": "image",
                "platinum": True,
            },
            "skill": {"id": "other", "tier": 1},
        },
    }
    responses = Responses({"data": [data], "nextCursor": None, "category": "skills"})
    model = (await Fishing(responses).skills.fetch()).items[0]
    assert model.skill_id == "skill" and model.tier == 2
    assert model.requirements.badge is not None and model.requirements.badge.platinum
    assert model.requirements.skill is not None and model.requirements.skill.tier == 1


@pytest.mark.asyncio
async def test_global_boost_end_properties_and_finite_multiplier() -> None:
    data: dict[str, object] = {
        "type": "xp",
        "multiplier": 2,
        "endsAt": "2000-01-01T00:00:00Z",
    }
    boost = (await GlobalBoosts(Responses({"data": [data]})).active())[0]
    assert boost.multiplier == 2.0 and not boost.is_active
    assert boost.time_remaining == timedelta(0)
    assert await GlobalBoosts(Responses({"data": []})).active() == ()
    data["multiplier"] = float("nan")
    with pytest.raises(DankMemerResponseError):
        await GlobalBoosts(Responses({"data": [data]})).active()


@pytest.mark.asyncio
async def test_merchant_rewards_keep_unknown_fields_and_convert_cost() -> None:
    data = {
        "date": "2026-10-01T00:00:00Z",
        "generatedAt": "2026-09-27T00:00:00Z",
        "trades": [
            {
                "position": "left",
                "max": 3,
                "reward": {
                    "type": "future-reward",
                    "quantity": 2,
                    "newReward": {"values": [1, None]},
                },
                "forReward": {"type": "item", "item": 12, "quantity": 3},
            }
        ],
    }
    responses = Responses({"data": data})
    rotation = await MerchantTrades(
        responses, items=Items(responses), baits=Baits(responses)
    ).today()
    assert rotation is not None
    assert rotation.date.tzinfo is UTC
    trade = rotation.trades[0]
    assert trade.cost.item_id == 12 and trade.cost.quantity == 3
    assert trade.reward.quantity == 2 and trade.reward.bait_id is None
    assert trade.reward.data["newReward"] == {"values": (1, None)}
    with pytest.raises(TypeError):
        cast(dict[str, object], trade.reward.data)["quantity"] = 4


@pytest.mark.asyncio
async def test_latest_resources_accept_only_explicit_null_as_absence() -> None:
    assert await Lottery(Responses({"data": None})).latest() is None
    responses = Responses({"data": None}, {"data": []})
    merchant = MerchantTrades(responses, items=Items(responses), baits=Baits(responses))
    assert await merchant.today() is None
    with pytest.raises(DankMemerResponseError):
        await Lottery(Responses({})).latest()
    with pytest.raises(DankMemerResponseError):
        await merchant.today()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [True, False])
async def test_ban_lookup_keeps_integer_id_and_boolean_result(status: bool) -> None:
    responses = Responses(status)
    assert await Users(responses).is_banned(123456789012345678) is status
    assert responses.calls == [
        Call(USER_BAN_STATUS, 123456789012345678, None, None, None, False)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", 0, 1, {"data": False}])
async def test_ban_lookup_does_not_coerce_invalid_data(value: object) -> None:
    with pytest.raises(DankMemerResponseError) as caught:
        await Users(Responses(value)).is_banned(123)
    assert caught.value.path == "/users/{user_id}/ban-status"
    assert "123" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_user_id_does_not_make_a_request() -> None:
    responses = Responses()
    lookup = cast(Callable[..., Awaitable[object]], Users(responses).is_banned)
    for value in (True, 0, -1, "123"):
        with pytest.raises(ConfigurationError):
            await lookup(value)
    assert responses.calls == []


@pytest.mark.asyncio
async def test_public_client_requests_use_the_shared_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        requested.append(request.path)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.path.endswith("/items"):
            assert dict(request.query) == {"limit": "1", "cursor": "0"}
            return web.json_response({"data": [item()], "nextCursor": 1})
        return web.json_response(False)

    app = web.Application()
    app.router.add_get("/api/v1/{tail:.*}", handler)
    async with TestServer(app) as server:
        monkeypatch.setattr(
            "dankmemer.http._transport._API_BASE_URL", str(server.make_url("/api/v1"))
        )
        async with DankMemer("test-token") as client:
            assert requested == []
            page = await client.items.fetch(page_size=1)
            assert isinstance(page.items[0], Item) and page.next_cursor == 1
            assert await client.users.is_banned(123) is False
        assert client.is_closed
    assert requested == ["/api/v1/items", "/api/v1/users/123/ban-status"]


def test_public_exports_and_resource_signatures() -> None:
    assert all(hasattr(dankmemer, name) for name in dankmemer.__all__)
    client = DankMemer("test-token")
    assert set(inspect.signature(client.items.fetch).parameters) == {
        "page_size",
        "cursor",
        "refresh",
    }
    assert set(inspect.signature(client.items.iter).parameters) == {
        "page_size",
        "cursor",
        "max_limit",
        "refresh",
    }
    assert set(inspect.signature(client.users.is_banned).parameters) == {"user_id"}
    assert not hasattr(client.items, "list")


async def check_public_types(client: DankMemer) -> None:
    assert_type(await client.items.get("Bank Note"), Item | None)
    assert_type(await client.items.get_by_id(12), Item | None)
    assert_type(await client.items.get_many_by_id(12, 13), tuple[Item | None, ...])
    assert_type(await client.items.get_by_key("bank-note"), Item | None)
    assert_type(await client.items.search("bank"), tuple[Item, ...])
    assert_type(await client.items.filter(min_market_value=100), tuple[Item, ...])
    assert_type(await client.pets.get("Cat"), Pet | None)
    assert_type(await client.pets.get_by_id("cat"), Pet | None)
    assert_type(await client.pets.friendly_to("Cat"), tuple[Pet, ...])
    assert_type(
        await client.skins.get("Classic", type="item", reference=12), Skin | None
    )
    assert_type(await client.commands.get("fish"), Command | None)
    assert_type(await client.blogs.latest(), Blog | None)
    assert_type(await client.blogs.search("update"), tuple[Blog, ...])
    assert_type(await client.blogs.get_by_id("test", scan_limit=50), Blog | None)
    assert_type(await client.changelogs.filter(scan_limit=50), tuple[Changelog, ...])
    assert_type(await client.fishing.creatures.get("Fish"), FishingCreature | None)
    assert_type(
        await client.fishing.creatures.search("fish"), tuple[FishingCreature, ...]
    )
    assert_type(await client.fishing.creatures_at("River"), tuple[FishingCreature, ...])
    assert_type(await client.fishing.skills.get("Skill", tier=1), FishingSkill | None)
    assert_type(await client.fishing.skills.tiers("skill"), tuple[FishingSkill, ...])
    assert_type(
        await client.merchant_trades.for_item("Bank Note"),
        tuple[dankmemer.MerchantTrade, ...],
    )
    assert_type(await client.items.fetch(), Page[Item])
    assert_type(client.items.iter(), AsyncIterator[Item])
    assert_type(await client.pets.fetch(), Page[Pet])
    assert_type(await client.skins.fetch(), Page[Skin])
    assert_type(await client.commands.fetch(), Page[Command])
    assert_type(await client.blogs.fetch(), Page[Blog])
    assert_type(await client.changelogs.fetch(), Page[Changelog])
    assert_type(await client.fishing.creatures.fetch(), Page[FishingCreature])
    assert_type(await client.fishing.locations.fetch(), Page[FishingLocation])
    assert_type(await client.fishing.npcs.fetch(), Page[FishingNPC])
    assert_type(await client.fishing.tools.fetch(), Page[FishingTool])
    assert_type(await client.fishing.baits.fetch(), Page[FishingBait])
    assert_type(await client.fishing.buckets.fetch(), Page[FishingBucket])
    assert_type(await client.fishing.skills.fetch(), Page[FishingSkill])
    assert_type(await client.global_boosts.active(), tuple[GlobalBoost, ...])
    assert_type(await client.lottery.latest(), LotteryResult | None)
    assert_type(await client.merchant_trades.today(), MerchantRotation | None)
    assert_type(await client.stream.trending_game(), str)
    assert_type(await client.users.is_banned(123), bool)
