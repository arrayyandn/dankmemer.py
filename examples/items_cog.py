from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from dankmemer import AmbiguousLookupError, DankMemerError, DankMemerTimeoutError
from dankmemer.ext.dpy import ItemAutocomplete

if TYPE_CHECKING:
    from .bot import DankBot


class ItemsCog(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot
        self.suggestions: ItemAutocomplete

    async def cog_load(self) -> None:
        self.suggestions = await ItemAutocomplete.create(self.bot.dank.items)

    @app_commands.command(description="Show an item's prices and description")
    async def item(
        self, interaction: discord.Interaction[commands.Bot], name: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        item = await self.suggestions.resolve(name)
        if item is None:
            await interaction.followup.send("Item not found.", ephemeral=True)
            return
        embed = discord.Embed(title=item.name[:256], description=item.details[:4096])
        embed.add_field(name="Market value", value=f"{item.market_value:,}")
        embed.add_field(name="Sell value", value=f"{item.sell_value:,}")
        embed.set_thumbnail(url=item.image_url)
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @item.autocomplete("name")
    async def item_autocomplete(
        self, interaction: discord.Interaction[commands.Bot], current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(current)

    async def _choices(self, current: str) -> list[app_commands.Choice[str]]:
        try:
            return await self.suggestions.choices(current, fuzzy=True)
        except DankMemerError:
            logging.getLogger(__name__).exception("Item autocomplete lookup failed")
            return []

    @app_commands.command(description="Find up to ten matching item names")
    async def search_items(
        self, interaction: discord.Interaction[commands.Bot], query: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        matches = await self.bot.dank.items.search(query, fuzzy=True, max_limit=10)
        text = "\n".join(
            f"{item.name[:120]} — {item.market_value:,}" for item in matches
        )
        await interaction.followup.send(
            text or "No matching items.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="Show the skins belonging to an item")
    async def item_skins(
        self, interaction: discord.Interaction[commands.Bot], name: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        item = await self.suggestions.resolve(name)
        if item is None:
            await interaction.followup.send("Item not found.", ephemeral=True)
            return
        skins = await self.bot.dank.skins.for_item(item)
        text = "\n".join(skin.name[:120] for skin in skins[:10])
        await interaction.followup.send(
            text or "No skins are listed for this item.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @item_skins.autocomplete("name")
    async def skins_autocomplete(
        self, interaction: discord.Interaction[commands.Bot], current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(current)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction[discord.Client],
        error: app_commands.AppCommandError,
    ) -> None:
        cause = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        if isinstance(cause, AmbiguousLookupError):
            message = "Several items have that name. Select an autocomplete suggestion."
        elif isinstance(cause, DankMemerTimeoutError):
            message = "The lookup took too long. Please try again later."
        elif isinstance(cause, DankMemerError):
            logging.getLogger(__name__).error(
                "Dank Memer lookup failed",
                exc_info=(type(cause), cause, cause.__traceback__),
            )
            message = "The item service is unavailable. Please try again later."
        else:
            raise error
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: DankBot) -> None:
    await bot.add_cog(ItemsCog(bot))
