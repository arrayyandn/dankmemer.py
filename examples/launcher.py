import asyncio
import os

from dankmemer import DankMemer

from .bot import DankBot


async def main() -> None:
    bot = DankBot(
        DankMemer(os.environ["DANK_MEMER_API_TOKEN"]),
        alert_channel_id=int(os.environ["ALERT_CHANNEL_ID"]),
    )
    async with bot:
        await bot.load_extension(f"{__package__}.dank_cog")
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
