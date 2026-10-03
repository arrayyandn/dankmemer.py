from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import DankBot


class FishingCog(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot

    @app_commands.command(
        description="Show the creatures listed for a fishing location"
    )
    async def creatures(
        self, interaction: discord.Interaction[commands.Bot], location: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        found = await self.bot.dank.fishing.locations.get(location)
        if found is None:
            await interaction.followup.send("Location not found.", ephemeral=True)
            return
        creatures = await self.bot.dank.fishing.creatures_at(found)
        text = "\n".join(creature.name[:120] for creature in creatures[:10])
        embed = discord.Embed(
            title=found.name[:256], description=text or "No creatures are listed."
        )
        embed.set_footer(text=f"Showing up to 10 of {len(creatures)} catalog entries")
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(description="Find a creature and its locations and tools")
    async def creature(
        self, interaction: discord.Interaction[commands.Bot], name: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        creature = await self.bot.dank.fishing.creatures.get(name)
        if creature is None:
            await interaction.followup.send("Creature not found.", ephemeral=True)
            return
        locations = await self.bot.dank.fishing.resolve_locations(creature)
        tools = await self.bot.dank.fishing.resolve_tools(creature)
        embed = discord.Embed(
            title=creature.name[:256], description=creature.flavor[:1000]
        )
        embed.add_field(name="Rarity", value=creature.rarity[:256])
        embed.add_field(
            name="Locations",
            value=", ".join(location.name for location in locations)[:1024]
            or "None listed",
        )
        embed.add_field(
            name="Tools",
            value=", ".join(tool.name for tool in tools)[:1024] or "None listed",
        )
        starts_at, ends_at = creature.get_availability_window()
        embed.add_field(
            name="Time window (UTC)",
            value=f"{starts_at:%Y-%m-%d %H:%M} – {ends_at:%Y-%m-%d %H:%M}",
            inline=False,
        )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        description="Show the fishing events currently reported active"
    )
    async def fishing_events(
        self, interaction: discord.Interaction[commands.Bot]
    ) -> None:
        await interaction.response.defer(thinking=True)
        events = await self.bot.dank.fishing_events.active(premium_only=False)
        text = "\n".join(
            f"{event.name[:120]} — ends <t:{int(event.ends_at.timestamp())}:R>"
            for event in events[:10]
        )
        await interaction.followup.send(
            text or "No matching fishing events are active.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: DankBot) -> None:
    await bot.add_cog(FishingCog(bot))
