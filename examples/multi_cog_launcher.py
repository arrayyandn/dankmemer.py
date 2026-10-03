import asyncio
import os

from dankmemer import DankMemer

from .bot import DankBot


async def main() -> None:
    dank = DankMemer(os.environ["DANK_MEMER_API_TOKEN"])
    bot = DankBot(dank, alert_channel_id=int(os.environ["ALERT_CHANNEL_ID"]))
    async with bot:
        for extension in ("items_cog", "fishing_cog", "activities_cog", "alerts_cog"):
            await bot.load_extension(f"{__package__}.{extension}")
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
