import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from dankmemer import DankMemer, DankMemerError, StoreDailyGift
from dankmemer.ext.dpy import ItemAutocomplete


async def main() -> None:
    intents = discord.Intents.default()
    bot = commands.Bot(
        command_prefix=commands.when_mentioned, intents=intents, help_command=None
    )
    dank = DankMemer(os.environ["DANK_MEMER_API_TOKEN"])
    alert_channel_id = int(os.environ["ALERT_CHANNEL_ID"])
    suggestions: ItemAutocomplete

    @bot.tree.command(description="Look up a Dank Memer item by name")
    async def item(interaction: discord.Interaction[commands.Bot], name: str) -> None:
        await interaction.response.defer(thinking=True)
        result = await suggestions.resolve(name)
        if result is None:
            await interaction.followup.send(
                "No item has that exact name. Try another name.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"{result.name}: market value {result.market_value:,}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @item.autocomplete("name")
    async def item_autocomplete(
        interaction: discord.Interaction[commands.Bot], current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            return await suggestions.choices(current)
        except DankMemerError:
            logging.getLogger(__name__).exception("Item autocomplete lookup failed")
            return []

    @dank.event
    async def on_store_daily_gift(gift: StoreDailyGift) -> None:
        await bot.wait_until_ready()
        channel = bot.get_channel(alert_channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            await channel.send(
                f"Today's {gift.category} gift: {gift.metadata.render}",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async with bot, dank:
        suggestions = await ItemAutocomplete.create(dank.items)
        # Sync commands manually when their definitions change.
        # Syncing on every restart sends unnecessary requests to Discord.
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
