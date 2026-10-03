# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import assert_type, cast

import discord
import pytest
from discord.ext import commands
from event_helpers import lottery, offline_client, wait_count, wait_paused
from resource_helpers import Responses

from dankmemer import (
    ConfigurationError,
    DankMemer,
    Drop,
    EventConfig,
    EventDelivery,
    LifecycleError,
    LotteryResult,
    PollingResource,
)
from dankmemer.ext.dpy import dank_cog
from dankmemer.storage.sqlite import SqliteEventStore


class LotteryCog(commands.Cog):
    def __init__(self, dank: DankMemer) -> None:
        self.dank = dank
        self.results: asyncio.Queue[LotteryResult] = asyncio.Queue()

    async def cog_load(self) -> None:
        dank_cog.bind(self, self.dank)

    async def cog_unload(self) -> None:
        dank_cog.unbind(self)

    @dank_cog.listen("lottery_result")
    async def received(self, result: LotteryResult) -> None:
        await self.results.put(result)


class PrimaryCog(commands.Cog):
    @dank_cog.event
    async def on_lottery_result(self, result: LotteryResult) -> None:
        pass


def test_decorators_preserve_callback_and_type_without_registering() -> None:
    async def on_drop_started(cog: commands.Cog, drop: Drop) -> None:
        pass

    typed = cast(Callable[[commands.Cog, Drop], Awaitable[None]], on_drop_started)
    decorated = dank_cog.event(typed)
    assert_type(decorated, Callable[[commands.Cog, Drop], Awaitable[None]])
    assert decorated is on_drop_started
    assert PrimaryCog.on_lottery_result.__name__ == "on_lottery_result"
    dank = DankMemer("test-token")
    assert not dank.active_polling_resources


@pytest.mark.asyncio
async def test_two_cogs_receive_one_poll_and_unbinding_keeps_other_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses(lottery(12))
    dank = offline_client(monkeypatch, responses, events=EventConfig(emit_initial=True))
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())

    class OtherLotteryCog(LotteryCog):
        pass

    first, second = LotteryCog(dank), OtherLotteryCog(dank)
    async with bot, dank:
        await bot.add_cog(first)
        await bot.add_cog(second)
        async with asyncio.timeout(3):
            assert (await first.results.get()).drawn_at.hour == 12
            assert (await second.results.get()).drawn_at.hour == 12
        assert len(responses.calls) == 1
        assert dank.active_polling_resources == frozenset({PollingResource.LOTTERY})
        await bot.remove_cog("LotteryCog")
        assert dank.active_polling_resources == frozenset({PollingResource.LOTTERY})
        assert not dank.is_closed
        await bot.remove_cog("OtherLotteryCog")
        assert not dank.active_polling_resources


@pytest.mark.asyncio
async def test_repeated_bind_and_unbind_are_idempotent_and_do_not_make_requests() -> (
    None
):
    dank = DankMemer("test-token")
    cog = LotteryCog(dank)
    dank_cog.bind(cog, dank)
    dank_cog.bind(cog, dank)
    assert len(dank._events._registrations) == 1
    assert dank._session is None
    dank_cog.unbind(cog)
    dank_cog.unbind(cog)
    assert not dank.active_polling_resources
    await dank.close()


@pytest.mark.asyncio
async def test_primary_replacement_survives_unloading_original_cog() -> None:
    dank = DankMemer("test-token")
    first, second = PrimaryCog(), PrimaryCog()
    dank_cog.bind(first, dank)
    dank_cog.bind(second, dank)
    dank_cog.unbind(first)
    assert dank.remove_listener(second.on_lottery_result)
    assert not dank.remove_listener(first.on_lottery_result)
    dank_cog.unbind(second)
    await dank.close()


@pytest.mark.asyncio
async def test_unload_does_not_restore_previous_primary_handler() -> None:
    dank = DankMemer("test-token")

    @dank.event
    async def on_lottery_result(result: LotteryResult) -> None:
        pass

    cog = PrimaryCog()
    dank_cog.bind(cog, dank)
    dank_cog.unbind(cog)
    assert not dank.active_polling_resources
    assert not dank.remove_listener(on_lottery_result)
    await dank.close()


@pytest.mark.asyncio
async def test_failed_batch_leaves_existing_primary_and_demand_unchanged() -> None:
    dank = DankMemer("test-token")

    @dank.event
    async def on_lottery_result(result: LotteryResult) -> None:
        pass

    class InvalidCog(commands.Cog):
        @dank_cog.event(subscription_id="duplicate")
        async def on_lottery_result(self, result: LotteryResult) -> None:
            pass

        @dank_cog.listen("drop_started", subscription_id="duplicate")
        async def drop(self, drop: Drop) -> None:
            pass

    with pytest.raises(ConfigurationError, match="distinct subscription_ids"):
        dank_cog.bind(InvalidCog(), dank)
    assert dank.active_polling_resources == frozenset({PollingResource.LOTTERY})
    assert dank.remove_listener(on_lottery_result)
    await dank.close()


@pytest.mark.asyncio
async def test_existing_subscription_conflict_does_not_partly_bind_cog() -> None:
    dank = DankMemer("test-token")

    @dank.listen("drop_started", subscription_id="already.used")
    async def existing(drop: Drop) -> None:
        pass

    class InvalidCog(PrimaryCog):
        @dank_cog.listen("drop_started", subscription_id="already.used")
        async def drop(self, drop: Drop) -> None:
            pass

    with pytest.raises(ConfigurationError, match="already registered"):
        dank_cog.bind(InvalidCog(), dank)
    assert dank.active_polling_resources == frozenset({PollingResource.DROPS})
    assert dank.remove_listener(existing, name="drop_started")
    await dank.close()


@pytest.mark.asyncio
async def test_inheritance_overrides_and_discovery_leave_properties_alone() -> None:
    class InheritedCog(PrimaryCog):
        @property
        def unrelated(self) -> int:
            raise AssertionError("listener discovery must not read this property")

    class OverriddenCog(PrimaryCog):
        async def on_lottery_result(self, result: LotteryResult) -> None:
            pass

    dank = DankMemer("test-token")
    inherited = InheritedCog()
    dank_cog.bind(inherited, dank)
    assert dank.remove_listener(inherited.on_lottery_result)
    dank_cog.unbind(inherited)
    overridden = OverriddenCog()
    dank_cog.bind(overridden, dank)
    assert not dank.active_polling_resources
    dank_cog.unbind(overridden)
    await dank.close()


@pytest.mark.asyncio
async def test_stacked_listeners_register_distinct_events_on_one_resource() -> None:
    class DropsCog(commands.Cog):
        @dank_cog.listen("drop_started")
        @dank_cog.listen("drop_ended")
        async def drop(self, drop: Drop) -> None:
            pass

    dank = DankMemer("test-token")
    cog = DropsCog()
    dank_cog.bind(cog, dank)
    assert dank.active_polling_resources == frozenset({PollingResource.DROPS})
    assert dank.remove_listener(cog.drop, name="drop_started")
    assert dank.remove_listener(cog.drop, name="drop_ended")
    dank_cog.unbind(cog)
    await dank.close()


@pytest.mark.asyncio
async def test_binding_rejects_wrong_signature_and_static_method() -> None:
    class WrongSignature(commands.Cog):
        @dank_cog.event
        async def on_drop_updated(self, drop: Drop) -> None:
            pass

    class StaticCog(commands.Cog):
        @staticmethod
        @dank_cog.event
        async def on_drop_started(drop: Drop) -> None:
            pass

    dank = DankMemer("test-token")
    for cog in (WrongSignature(), StaticCog()):
        with pytest.raises(ConfigurationError):
            dank_cog.bind(cog, dank)
        assert not dank.active_polling_resources
    await dank.close()


@pytest.mark.asyncio
async def test_cog_requires_unbinding_before_switching_clients() -> None:
    first, second = DankMemer("test-token"), DankMemer("test-token")
    cog = PrimaryCog()
    dank_cog.bind(cog, first)
    with pytest.raises(ConfigurationError, match="another client"):
        dank_cog.bind(cog, second)
    assert not second.active_polling_resources
    dank_cog.unbind(cog)
    dank_cog.bind(cog, second)
    assert second.active_polling_resources == frozenset({PollingResource.LOTTERY})
    await second.close()
    dank_cog.unbind(cog)
    with pytest.raises(LifecycleError):
        dank_cog.bind(cog, second)
    await first.close()


def test_decorators_reject_unknown_events_sync_callbacks_and_duplicate_marks() -> None:
    async def on_drop_started(cog: commands.Cog, drop: Drop) -> None:
        pass

    with pytest.raises(ConfigurationError, match="unknown event"):
        dank_cog.listen("unknown")(on_drop_started)
    with pytest.raises(ConfigurationError, match="nonempty"):
        dank_cog.listen("drop_started", subscription_id=" ")(on_drop_started)
    with pytest.raises(ConfigurationError, match="async methods"):
        dank_cog.listen("drop_started")(cast(Callable[..., Awaitable[None]], print))
    dank_cog.event(on_drop_started)
    with pytest.raises(ConfigurationError, match="twice"):
        dank_cog.listen("drop_started")(on_drop_started)


@pytest.mark.asyncio
async def test_durable_listeners_require_ids_before_replacing_any_primary(
    tmp_path: Path,
) -> None:
    async with await SqliteEventStore.open(tmp_path / "cogs.sqlite") as store:
        dank = DankMemer(
            "test-token",
            events=EventConfig(delivery=EventDelivery.DURABLE),
            event_store=store,
        )

        @dank.event
        async def on_lottery_result(result: LotteryResult) -> None:
            pass

        with pytest.raises(ConfigurationError, match="stable subscription_id"):
            dank_cog.bind(LotteryCog(dank), dank)
        assert dank.remove_listener(on_lottery_result)
        await dank.close()


@pytest.mark.asyncio
async def test_durable_cog_replays_saved_callback_after_reload_and_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingCog(LotteryCog):
        @dank_cog.listen("lottery_result", subscription_id="cog.lottery")
        async def received(self, result: LotteryResult) -> None:
            raise RuntimeError("notification channel unavailable")

    class RecoveredCog(LotteryCog):
        @dank_cog.listen("lottery_result", subscription_id="cog.lottery")
        async def received(self, result: LotteryResult) -> None:
            await self.results.put(result)

    config = EventConfig(delivery=EventDelivery.DURABLE, emit_initial=True)
    async with await SqliteEventStore.open(tmp_path / "replay.sqlite") as store:
        first = offline_client(
            monkeypatch, Responses(lottery(12)), events=config, event_store=store
        )
        old = FailingCog(first)
        await old.cog_load()
        async with first:
            await wait_paused(first, "cog.lottery")
            await old.cog_unload()
            assert await first.count_pending_events(subscription_id="cog.lottery") == 1
        second = offline_client(
            monkeypatch, Responses(lottery(12)), events=config, event_store=store
        )
        recovered = RecoveredCog(second)
        await recovered.cog_load()
        async with second:
            async with asyncio.timeout(3):
                assert (await recovered.results.get()).drawn_at.hour == 12
            await wait_count(store, 0)
        await recovered.cog_unload()
