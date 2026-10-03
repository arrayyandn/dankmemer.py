import discord
from discord.ext import commands

from dankmemer import DankMemer


class DankBot(commands.Bot):
    def __init__(self, dank: DankMemer, *, alert_channel_id: int) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            help_command=None,
        )
        self.dank = dank
        self.alert_channel_id = alert_channel_id

    async def setup_hook(self) -> None:
        await self.dank.start()
        # Sync commands manually when their definitions change.
        # Syncing on every restart sends unnecessary requests to Discord.

    async def close(self) -> None:
        try:
            await self.dank.close()
        finally:
            await super().close()
