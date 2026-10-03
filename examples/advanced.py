import asyncio
import logging
import os
from contextlib import AsyncExitStack

import aiohttp

from dankmemer import (
    ApplicationIdentity,
    DankMemer,
    Drop,
    EventConfig,
    EventDelivery,
    PollingConfig,
    RequestConfig,
)
from dankmemer.storage.sqlite import SqliteEventStore


async def on_drop_started(drop: Drop) -> None:
    logging.getLogger("example").info("Drop %s ends at %s", drop.id, drop.ends_at)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(aiohttp.ClientSession())
        store = await stack.enter_async_context(
            await SqliteEventStore.open("events.sqlite")
        )
        dank = DankMemer(
            os.environ["DANK_MEMER_API_TOKEN"],
            session=session,
            application=ApplicationIdentity(name="drop-notifier", version="1.0"),
            request=RequestConfig(total_timeout_seconds=120),
            polling=PollingConfig(startup_jitter_seconds=5),
            events=EventConfig(delivery=EventDelivery.DURABLE),
            event_store=store,
        )
        stack.push_async_callback(dank.close)
        dank.add_listener(
            on_drop_started,
            name="drop_started",
            subscription_id="drop-notifier.alerts",
        )
        await dank.start()
        print("Watching drops with durable replay. Press Ctrl+C to stop.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
