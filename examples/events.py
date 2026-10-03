import asyncio
import os

from dankmemer import DankMemer, Drop, EventConfig


async def main() -> None:
    dank = DankMemer(
        os.environ["DANK_MEMER_API_TOKEN"],
        events=EventConfig(emit_initial=True),
    )

    @dank.event
    async def on_drop_started(drop: Drop) -> None:
        print(f"Drop {drop.id} is available until {drop.ends_at}")

    async with dank:
        print("Watching drops. Press Ctrl+C to stop.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
