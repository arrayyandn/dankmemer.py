from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from dankmemer import DankMemerError, Drop
from dankmemer.ext.dpy import ItemAutocomplete, dank_cog

if TYPE_CHECKING:
    from .bot import DankBot


class DankCog(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot
        self.suggestions: ItemAutocomplete

    async def cog_load(self) -> None:
        self.suggestions = await ItemAutocomplete.create(self.bot.dank.items)
        dank_cog.bind(self, self.bot.dank)

    async def cog_unload(self) -> None:
        dank_cog.unbind(self)

    @dank_cog.listen("drop_started", subscription_id="cog.drop_alerts")
    async def on_drop_started(self, drop: Drop) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.bot.alert_channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            await channel.send(
                f"Drop {drop.id} is available until {drop.ends_at:%H:%M} UTC.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(description="Show today's Dank Memer daily gifts")
    async def gifts(self, interaction: discord.Interaction[commands.Bot]) -> None:
        await interaction.response.defer(thinking=True)
        gifts = await self.bot.dank.store_daily_gifts.today()
        if not gifts:
            await interaction.followup.send("No daily gifts are available.")
            return
        await interaction.followup.send(
            "\n".join(f"{gift.category}: {gift.metadata.render}" for gift in gifts),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="Find an item's market value")
    async def price(
        self, interaction: discord.Interaction[commands.Bot], name: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        item = await self.suggestions.resolve(name)
        if item is None:
            await interaction.followup.send(
                "No item has that exact name. Try another name.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"{item.name}: market value {item.market_value:,}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @price.autocomplete("name")
    async def price_autocomplete(
        self, interaction: discord.Interaction[commands.Bot], current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            return await self.suggestions.choices(current)
        except DankMemerError:
            logging.getLogger(__name__).exception("Item autocomplete lookup failed")
            return []


async def setup(bot: DankBot) -> None:
    cog = DankCog(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        # Slash command registration can fail after cog_load has bound listeners.
        dank_cog.unbind(cog)
        raise
