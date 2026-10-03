import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import assert_type, cast

import pytest
from discord import app_commands
from resource_helpers import ManualClock, Responses, item, page

from dankmemer import (
    AmbiguousLookupError,
    ConfigurationError,
    DankMemer,
    Item,
    LifecycleError,
    NoCache,
    NotFound,
    TTLCache,
)
from dankmemer.ext.dpy import ItemAutocomplete
from dankmemer.resources import Items


@pytest.mark.asyncio
async def test_preload_choices_and_resolution_share_the_item_catalog() -> None:
    responses = Responses(
        page(item(1, "Bank Note"), next_cursor=1), page(item(2, "Life Saver"))
    )
    resource = Items(responses, cache=TTLCache(ttl=timedelta(hours=1)))
    helper = await ItemAutocomplete.create(resource)
    assert_type(helper, ItemAutocomplete)
    assert len(responses.calls) == 2
    choices = await helper.choices("life")
    assert_type(choices, list[app_commands.Choice[str]])
    assert [(choice.name, choice.value) for choice in choices] == [
        ("Life Saver", "id:2")
    ]
    resolved = await helper.resolve(choices[0].value)
    assert_type(resolved, Item | None)
    assert resolved is await resource.get("Life Saver")
    assert await helper.resolve(" LIFE SAVER ") is resolved
    assert (await helper.choices("life sver", fuzzy=True))[0].value == "id:2"
    assert [choice.name for choice in await helper.choices(" \t")] == [
        "Bank Note",
        "Life Saver",
    ]
    assert await helper.resolve("life sver") is None
    assert await helper.resolve("id:99") is None
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_skipped_preload_fetches_on_first_choices_call_by_default() -> None:
    responses = Responses(page(item(1, "Life Saver")))
    resource = Items(responses, cache=TTLCache(ttl=timedelta(hours=1)))
    helper = await ItemAutocomplete.create(resource, preload=False)
    assert await helper.choices("life", fetch=False) == []
    assert not responses.calls
    assert (await helper.choices("life"))[0].value == "id:1"
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_expiry_and_resource_refresh_are_visible_to_choices_and_resolution() -> (
    None
):
    clock = ManualClock()
    responses = Responses(
        page({**item(1, "Life Saver"), "marketValue": 100}),
        page({**item(1, "Life Saver"), "marketValue": 200}),
        page({**item(1, "Life Saver"), "marketValue": 300}),
    )
    resource = Items(responses, cache=TTLCache(ttl=timedelta(hours=1)), clock=clock)
    helper = await ItemAutocomplete.create(resource)
    clock.elapsed = 3600
    assert await helper.choices("life", fetch=False) == []
    assert len(responses.calls) == 1
    selected = await helper.resolve("id:1")
    assert selected is not None and selected.market_value == 200
    updated = await resource.get("Life Saver", refresh=True)
    assert updated is not None and updated.market_value == 300
    assert (await helper.choices("life", fetch=False))[0].value == "id:1"
    assert await helper.resolve("id:1") is updated
    assert len(responses.calls) == 3
    resource.clear_cache()
    assert await helper.choices("", fetch=False) == []


@pytest.mark.asyncio
async def test_no_cache_does_not_retain_suggestions() -> None:
    responses = Responses(page(item(1, "Bank Note")), page(item(2, "Life Saver")))
    helper = await ItemAutocomplete.create(Items(responses, cache=NoCache()))
    assert await helper.choices("", fetch=False) == []
    assert (await helper.choices(""))[0].value == "id:2"
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_discord_limits_ranking_and_duplicate_names_resolve_by_id() -> None:
    records = [
        item(1, "Bank"),
        item(2, "Bank Note"),
        item(3, "Gold Bank"),
        item(4, "Bank Note"),
        item(40, "Z" * 150),
        *(item(number, f"Other {number}") for number in range(5, 40)),
    ]
    resource = Items(Responses(page(*records)), cache=TTLCache(ttl=timedelta(hours=1)))
    helper = await ItemAutocomplete.create(resource)
    assert [choice.value for choice in await helper.choices("bank")] == [
        "id:1",
        "id:2",
        "id:4",
        "id:3",
    ]
    assert len(await helper.choices("")) == 25
    assert (await helper.choices("zzz"))[0].name == "Z" * 100
    second = await helper.resolve("id:2")
    fourth = await helper.resolve("id:4")
    assert second is not None and fourth is not None and second.id != fourth.id
    with pytest.raises(AmbiguousLookupError):
        await helper.resolve("Bank Note")


@pytest.mark.asyncio
async def test_cancelling_a_waiter_does_not_cancel_shared_catalog_loading() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def request(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return page(item(1, "Life Saver"))

    helper = await ItemAutocomplete.create(
        Items(request, cache=TTLCache(ttl=timedelta(hours=1))), preload=False
    )
    first = asyncio.create_task(helper.choices("life"))
    await entered.wait()
    second = asyncio.create_task(helper.choices("saver"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await helper.choices("", fetch=False) == []
    release.set()
    assert (await second)[0].name == "Life Saver"
    assert calls == 1


@pytest.mark.asyncio
async def test_two_second_limit_returns_no_choices_but_keeps_shared_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original_timeout = asyncio.timeout
    timeouts: list[asyncio.Timeout] = []

    def controlled_timeout(delay: float | None) -> asyncio.Timeout:
        assert delay == 2
        timeout = original_timeout(None)
        timeouts.append(timeout)
        return timeout

    monkeypatch.setattr(
        "dankmemer.ext.dpy._autocomplete.asyncio.timeout", controlled_timeout
    )

    async def request(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return page(item(1, "Life Saver"))

    resource = Items(request, cache=TTLCache(ttl=timedelta(hours=1)))
    helper = await ItemAutocomplete.create(resource, preload=False)
    choices = asyncio.create_task(helper.choices("life"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    # Expire the real timeout without waiting two wall-clock seconds.
    timeouts[0].reschedule(asyncio.get_running_loop().time() - 1)
    assert await choices == []
    release.set()
    result = await helper.resolve("id:1")
    assert result is not None and result.name == "Life Saver"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [{"current": None}, {"current": "", "fetch": 1}, {"current": "", "fuzzy": 1}],
)
async def test_invalid_choice_options_make_no_requests(
    options: dict[str, object],
) -> None:
    responses = Responses()
    helper = await ItemAutocomplete.create(Items(responses), preload=False)
    operation = cast(Callable[..., Awaitable[object]], helper.choices)
    with pytest.raises(ConfigurationError):
        await operation(**options)
    assert not responses.calls


@pytest.mark.asyncio
async def test_invalid_preload_makes_no_requests() -> None:
    responses = Responses()
    with pytest.raises(ConfigurationError):
        await ItemAutocomplete.create(Items(responses), preload=cast(bool, 1))
    assert not responses.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["id:", "id:-1", "id:invalid", "id:" + "1" * 5_000])
async def test_invalid_selected_id_makes_no_requests(value: str) -> None:
    responses = Responses()
    helper = await ItemAutocomplete.create(Items(responses), preload=False)
    assert await helper.resolve(value) is None
    assert not responses.calls


@pytest.mark.asyncio
async def test_choices_propagate_request_errors() -> None:
    error = NotFound("missing", status_code=404, path="/items")
    helper = await ItemAutocomplete.create(Items(Responses(error)), preload=False)
    with pytest.raises(NotFound) as caught:
        await helper.choices("life")
    assert caught.value is error


@pytest.mark.asyncio
async def test_helper_rejects_use_after_client_closes() -> None:
    client = DankMemer("test-token")
    helper = await ItemAutocomplete.create(client.items, preload=False)
    await client.close()
    with pytest.raises(LifecycleError):
        await helper.choices("", fetch=False)
    with pytest.raises(LifecycleError):
        await helper.resolve("id:invalid")
    with pytest.raises(LifecycleError):
        await ItemAutocomplete.create(client.items, preload=False)
