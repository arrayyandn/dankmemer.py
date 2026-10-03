# pyright: reportPrivateUsage=false

from collections.abc import Awaitable, Callable, Sequence
from types import SimpleNamespace
from typing import cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from resource_helpers import Responses, example, first_record

from dankmemer import DankMemer, DankMemerTimeoutError, PollingResource
from dankmemer.ext.dpy import ItemAutocomplete
from dankmemer.http._routes import Route
from examples import alerts_cog, dank_cog, discord_functions, explicit_cog
from examples.bot import DankBot
from examples.dank_cog import DankCog
from examples.items_cog import ItemsCog


class SentMessages:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.embeds: list[discord.Embed] = []

    async def send(
        self,
        content: str | None = None,
        *,
        ephemeral: bool = False,
        allowed_mentions: discord.AllowedMentions | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        if content is not None:
            self.messages.append(content)
        if embed is not None:
            self.embeds.append(embed)


class DeferredResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.ephemeral = False
        self.choices: tuple[app_commands.Choice[str | int | float], ...] = ()
        self.autocomplete_calls = 0

    async def defer(self, *, thinking: bool = False, ephemeral: bool = False) -> None:
        self.deferred = True
        self.ephemeral = ephemeral

    def is_done(self) -> bool:
        return self.deferred

    async def autocomplete(
        self, choices: Sequence[app_commands.Choice[str | int | float]]
    ) -> None:
        self.autocomplete_calls += 1
        self.choices = tuple(choices)


class ExampleInteraction:
    def __init__(self) -> None:
        self.response = DeferredResponse()
        self.followup = SentMessages()


async def failed_choices(
    helper: ItemAutocomplete,
    current: str,
    *,
    fetch: bool = True,
    fuzzy: bool = False,
) -> list[app_commands.Choice[str]]:
    raise DankMemerTimeoutError("catalog lookup timed out")


def documented_responses(monkeypatch: pytest.MonkeyPatch) -> list[DankMemer]:
    clients: list[DankMemer] = []

    async def request(
        client: DankMemer,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        if client not in clients:
            clients.append(client)
        key = route.template
        if category is not None:
            key += f"?category={category}"
        if category == "tools":
            # The separate published examples use different tool IDs.
            tool = {**first_record(key), "id": "example-tool"}
            return {"category": category, "data": [tool], "nextCursor": None}
        return example(key)

    monkeypatch.setattr(DankMemer, "_request_json", request)
    return clients


@pytest.mark.asyncio
async def test_bot_example_owns_lifecycle_without_commands_or_catalog_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = Responses()
    monkeypatch.setattr(DankMemer, "_request_json", responses)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)

    async with bot:
        await bot.setup_hook()
        assert bot.dank.is_running
        assert bot.tree.get_commands() == []
        assert not bot.dank.active_polling_resources
        assert not responses.calls
    assert bot.dank.is_closed
    assert bot.is_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("autocomplete_failure", [False, True])
async def test_function_example_registers_and_runs_slash_autocomplete(
    monkeypatch: pytest.MonkeyPatch,
    autocomplete_failure: bool,
) -> None:
    clients = documented_responses(monkeypatch)
    monkeypatch.setenv("DANK_MEMER_API_TOKEN", "example-token")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "example-discord-token")
    monkeypatch.setenv("ALERT_CHANNEL_ID", "123")
    bots: list[commands.Bot] = []

    async def start(bot: commands.Bot, token: str, *, reconnect: bool = True) -> None:
        bots.append(bot)
        await bot.setup_hook()
        command = bot.tree.get_command("item")
        assert isinstance(command, app_commands.Command)
        context = ExampleInteraction()
        interaction = cast(discord.Interaction[commands.Bot], context)
        namespace = cast(app_commands.Namespace, SimpleNamespace(name="example"))
        with monkeypatch.context() as patch:
            if autocomplete_failure:
                patch.setattr(ItemAutocomplete, "choices", failed_choices)
            await command._invoke_autocomplete(interaction, "name", namespace)
        assert context.response.autocomplete_calls == 1
        expected = [] if autocomplete_failure else [("Example item", "id:1")]
        assert [
            (choice.name, choice.value) for choice in context.response.choices
        ] == expected
        callback = cast(Callable[..., Awaitable[None]], command.callback)
        await callback(interaction, name="id:1")
        assert context.response.deferred
        assert context.followup.messages == ["Example item: market value 125"]
        await callback(interaction, name="Example item")
        assert context.followup.messages == ["Example item: market value 125"] * 2

    monkeypatch.setattr(commands.Bot, "start", start)
    await discord_functions.main()
    assert len(bots) == 1 and bots[0].is_closed()
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.asyncio
async def test_cog_slash_commands_and_bound_autocomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)

    async with bot:
        await bot.load_extension("examples.dank_cog")
        await bot.setup_hook()
        cog = bot.get_cog("DankCog")
        assert cog is not None
        gifts = bot.tree.get_command("gifts")
        price = bot.tree.get_command("price")
        assert isinstance(gifts, app_commands.Command)
        assert isinstance(price, app_commands.Command)
        assert gifts.binding is cog and price.binding is cog
        gift_context = ExampleInteraction()
        gift_callback = cast(Callable[..., Awaitable[None]], gifts.callback)
        await gift_callback(cog, cast(discord.Interaction[commands.Bot], gift_context))
        assert gift_context.response.deferred
        assert gift_context.followup.messages == ["daily: 1,000"]
        context = ExampleInteraction()
        interaction = cast(discord.Interaction[commands.Bot], context)
        namespace = cast(app_commands.Namespace, SimpleNamespace(name="example"))
        await price._invoke_autocomplete(interaction, "name", namespace)
        assert [(choice.name, choice.value) for choice in context.response.choices] == [
            ("Example item", "id:1")
        ]
        price_callback = cast(Callable[..., Awaitable[None]], price.callback)
        await price_callback(cog, interaction, name="id:1")
        assert context.response.deferred
        assert context.followup.messages == ["Example item: market value 125"]
        await price_callback(cog, interaction, name="Example item")
        assert context.followup.messages == ["Example item: market value 125"] * 2


@pytest.mark.asyncio
async def test_cog_extension_reload_removes_old_listener_and_retains_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    async with bot:
        await bot.load_extension("examples.dank_cog")
        loaded = bot.get_cog("DankCog")
        assert loaded is not None
        first = cast(DankCog, loaded)
        assert bot.tree.get_command("gifts") is not None
        assert bot.tree.get_command("price") is not None
        assert bot.dank.active_polling_resources == frozenset({PollingResource.DROPS})

        await bot.reload_extension("examples.dank_cog")
        reloaded = bot.get_cog("DankCog")
        assert reloaded is not None
        second = cast(DankCog, reloaded)
        assert second is not first
        assert not bot.dank.remove_listener(first.on_drop_started, name="drop_started")

        await bot.unload_extension("examples.dank_cog")
        assert not bot.dank.remove_listener(second.on_drop_started, name="drop_started")
        assert not bot.dank.active_polling_resources
        assert bot.tree.get_command("gifts") is None
        assert bot.tree.get_command("price") is None
        assert not bot.dank.is_closed
    assert bot.dank.is_closed


@pytest.mark.asyncio
async def test_bot_shutdown_can_unload_cog_after_closing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    async with bot:
        await bot.load_extension("examples.dank_cog")
        loaded = bot.get_cog("DankCog")
        assert loaded is not None
        cog = cast(DankCog, loaded)
    assert bot.dank.is_closed
    assert not hasattr(cog, "__dankmemer_cog_binding__")
    assert bot.get_cog("DankCog") is None


@pytest.mark.asyncio
async def test_split_command_cogs_register_and_execute_documented_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    async with bot:
        for extension in ("items_cog", "fishing_cog", "activities_cog", "alerts_cog"):
            await bot.load_extension(f"examples.{extension}")
        assert bot.dank.active_polling_resources == frozenset(PollingResource)
        cases: tuple[tuple[str, dict[str, object]], ...] = (
            ("item", {"name": "id:1"}),
            ("search_items", {"query": "example"}),
            ("item_skins", {"name": "id:1"}),
            ("creatures", {"location": "River"}),
            ("creature", {"name": "Example fish"}),
            ("fishing_events", {}),
            ("gifts", {}),
            ("sales", {}),
            ("lottery", {}),
            ("merchant", {"name": "id:1"}),
            ("trending", {}),
            ("ban_status", {"user": SimpleNamespace(id=123456789012345678)}),
        )
        for name, arguments in cases:
            command = bot.tree.get_command(name)
            assert isinstance(command, app_commands.Command)
            context = ExampleInteraction()
            callback = cast(Callable[..., Awaitable[None]], command.callback)
            await callback(
                command.binding,
                cast(discord.Interaction[commands.Bot], context),
                **arguments,
            )
            assert context.response.deferred
            if name == "ban_status":
                assert context.response.ephemeral
            assert context.followup.messages or context.followup.embeds
            for embed in context.followup.embeds:
                assert len(embed) <= 6000
        assert not bot.dank.is_running
        await bot.unload_extension("examples.alerts_cog")
        assert not bot.dank.active_polling_resources
        assert bot.tree.get_command("merchant") is not None


@pytest.mark.asyncio
async def test_explicit_listener_extension_reloads_without_accumulating_callbacks() -> (
    None
):
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    async with bot:
        await bot.load_extension("examples.explicit_cog")
        assert bot.dank.active_polling_resources == frozenset({PollingResource.DROPS})
        await bot.reload_extension("examples.explicit_cog")
        assert len(bot.dank._events._registrations) == 1
        await bot.unload_extension("examples.explicit_cog")
        assert not bot.dank.active_polling_resources


@pytest.mark.asyncio
async def test_cog_slash_registration_failure_removes_sdk_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)

    @bot.tree.command(name="price")
    async def existing(interaction: discord.Interaction[commands.Bot]) -> None:
        pass

    async with bot:
        with pytest.raises(commands.ExtensionFailed) as raised:
            await bot.load_extension("examples.dank_cog")
        assert isinstance(raised.value.original, app_commands.CommandAlreadyRegistered)
        assert bot.get_cog("DankCog") is None
        assert bot.tree.get_command("price") is existing
        assert bot.tree.get_command("gifts") is None
        assert not bot.dank.active_polling_resources
        assert not bot.dank._events._registrations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup", [dank_cog.setup, alerts_cog.setup, explicit_cog.setup]
)
async def test_listener_extension_setup_cleans_up_after_installation_failure(
    monkeypatch: pytest.MonkeyPatch,
    setup: Callable[[DankBot], Awaitable[None]],
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    loaded: list[commands.Cog] = []

    async def fail_after_load(cog: commands.Cog) -> None:
        await cog.cog_load()
        loaded.append(cog)
        assert bot.dank.active_polling_resources
        raise RuntimeError("Cog installation failed after loading")

    monkeypatch.setattr(bot, "add_cog", fail_after_load)
    async with bot:
        with pytest.raises(RuntimeError, match="installation failed"):
            await setup(bot)
        assert len(loaded) == 1
        assert not hasattr(loaded[0], "__dankmemer_cog_binding__")
        assert not bot.dank.active_polling_resources
        assert not bot.dank._events._registrations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extension", "command_names"),
    [
        ("items_cog", ("item", "item_skins")),
        ("dank_cog", ("price",)),
        ("activities_cog", ("merchant",)),
    ],
)
async def test_cog_recipes_answer_autocomplete_after_sdk_failure(
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    command_names: tuple[str, ...],
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)

    async with bot:
        await bot.load_extension(f"examples.{extension}")
        monkeypatch.setattr(ItemAutocomplete, "choices", failed_choices)
        for name in command_names:
            command = bot.tree.get_command(name)
            assert isinstance(command, app_commands.Command)
            context = ExampleInteraction()
            await command._invoke_autocomplete(
                cast(discord.Interaction[commands.Bot], context),
                "name",
                cast(app_commands.Namespace, SimpleNamespace(name="example")),
            )
            assert context.response.autocomplete_calls == 1
            assert context.response.choices == ()


@pytest.mark.asyncio
async def test_command_recipe_reports_a_timeout_after_deferring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documented_responses(monkeypatch)
    bot = DankBot(DankMemer("example-token"), alert_channel_id=123)
    async with bot:
        cog = ItemsCog(bot)
        context = ExampleInteraction()
        await context.response.defer()
        await cog.cog_app_command_error(
            cast(discord.Interaction[discord.Client], context),
            app_commands.CommandInvokeError(
                cog.item, DankMemerTimeoutError("lookup timed out")
            ),
        )
        assert len(context.followup.messages) == 1
        assert context.followup.messages[0]
