from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from dankmemer import DankMemerError
from dankmemer.ext.dpy import ItemAutocomplete

if TYPE_CHECKING:
    from .bot import DankBot


class ActivitiesCog(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot
        self.suggestions: ItemAutocomplete

    async def cog_load(self) -> None:
        self.suggestions = await ItemAutocomplete.create(self.bot.dank.items)

    @app_commands.command(description="Show today's free store gifts")
    async def gifts(self, interaction: discord.Interaction[commands.Bot]) -> None:
        await interaction.response.defer(thinking=True)
        gifts = await self.bot.dank.store_daily_gifts.today()
        text = "\n".join(
            f"{gift.category[:80]}: {gift.metadata.render[:160]}" for gift in gifts[:5]
        )
        await interaction.followup.send(
            text or "No daily gifts are available.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="Show current store sales")
    async def sales(self, interaction: discord.Interaction[commands.Bot]) -> None:
        await interaction.response.defer(thinking=True)
        sales = await self.bot.dank.store_sales.active(subject_kind="item")
        text = "\n".join(
            f"{sale.name[:120]} — {sale.price:g} — ends <t:{int(sale.ends_at.timestamp())}:R>"
            for sale in sales[:10]
        )
        await interaction.followup.send(
            text or "No item sales are active.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="Show the latest completed lottery drawing")
    async def lottery(self, interaction: discord.Interaction[commands.Bot]) -> None:
        await interaction.response.defer(thinking=True)
        result = await self.bot.dank.lottery.latest()
        if result is None:
            await interaction.followup.send("No completed drawing is available.")
            return
        await interaction.followup.send(
            f"Drawn <t:{int(result.drawn_at.timestamp())}:f>\n"
            f"Winnings: {result.winnings:,}\n"
            f"Entries: {result.total_entries:,}; participants: {result.participants:,}"
        )

    @app_commands.command(description="Find merchant offers requiring an item")
    async def merchant(
        self, interaction: discord.Interaction[commands.Bot], name: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        item = await self.suggestions.resolve(name)
        if item is None:
            await interaction.followup.send("Item not found.", ephemeral=True)
            return
        offers = await self.bot.dank.merchant_trades.for_item(item)
        lines: list[str] = []
        for offer in offers[:5]:
            reward = await self.bot.dank.merchant_trades.resolve_reward(offer)
            label = reward.name if reward is not None else offer.reward.type
            lines.append(
                f"{offer.position[:30]}: {offer.cost.quantity} × {item.name[:100]} "
                f"for {offer.reward.quantity} × {label[:100]} (limit {offer.max})"
            )
        await interaction.followup.send(
            "\n".join(lines) or "No current offers require that item.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @merchant.autocomplete("name")
    async def merchant_autocomplete(
        self, interaction: discord.Interaction[commands.Bot], current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            return await self.suggestions.choices(current)
        except DankMemerError:
            logging.getLogger(__name__).exception("Item autocomplete lookup failed")
            return []

    @app_commands.command(description="Show the API's current streaming game")
    async def trending(self, interaction: discord.Interaction[commands.Bot]) -> None:
        await interaction.response.defer(thinking=True)
        game = await self.bot.dank.stream.trending_game()
        await interaction.followup.send(
            game[:1900], allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        description="Check Dank Memer's reported ban status for a user"
    )
    async def ban_status(
        self, interaction: discord.Interaction[commands.Bot], user: discord.User
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        banned = await self.bot.dank.users.is_banned(user.id)
        await interaction.followup.send(
            f"Dank Memer ban status: {'banned' if banned else 'not banned'}.",
            ephemeral=True,
        )


async def setup(bot: DankBot) -> None:
    await bot.add_cog(ActivitiesCog(bot))
