import asyncio
import os

from dankmemer import DankMemer


async def main() -> None:
    async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
        item = await dank.items.get("Life Saver")
        if item is not None:
            print(f"{item.name}: market value {item.market_value:,}")


if __name__ == "__main__":
    asyncio.run(main())
