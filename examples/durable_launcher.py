import asyncio
import os

from dankmemer import DankMemer, EventConfig, EventDelivery
from dankmemer.storage.sqlite import SqliteEventStore

from .bot import DankBot


async def main() -> None:
    async with await SqliteEventStore.open("dank_events.sqlite") as store:
        dank = DankMemer(
            os.environ["DANK_MEMER_API_TOKEN"],
            events=EventConfig(delivery=EventDelivery.DURABLE),
            event_store=store,
        )
        bot = DankBot(dank, alert_channel_id=int(os.environ["ALERT_CHANNEL_ID"]))
        async with bot:
            await bot.load_extension(f"{__package__}.alerts_cog")
            await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
