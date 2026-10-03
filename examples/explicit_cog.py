from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from dankmemer import Drop

if TYPE_CHECKING:
    from .bot import DankBot


class DropAlerts(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot
        self.listener_id: int | None = None

    async def cog_load(self) -> None:
        self.listener_id = self.bot.dank.add_listener(
            self.on_drop_started,
            name="drop_started",
            subscription_id="alerts.drop_started",
        )

    async def cog_unload(self) -> None:
        if self.listener_id is not None:
            # Bot shutdown may close the SDK before unloading this extension.
            if not self.bot.dank.is_closed:
                self.bot.dank.remove_listener(self.listener_id)
            self.listener_id = None

    async def on_drop_started(self, drop: Drop) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.bot.alert_channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            await channel.send(
                f"Drop {drop.id} ends <t:{int(drop.ends_at.timestamp())}:R>.",
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot: DankBot) -> None:
    cog = DropAlerts(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        await cog.cog_unload()
        raise
