import asyncio
import os

from dankmemer import DankMemer


async def main() -> None:
    async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
        item = await dank.items.get("Life Saver")
        if item is not None:
            print(f"{item.name}: market value {item.market_value:,}")
            for sale in await dank.store_sales.for_item(item):
                print(f"On sale for {sale.price:g}: {sale.url}")

        for gift in await dank.store_daily_gifts.today():
            print(f"{gift.category}: {gift.metadata.render}")
            reward_item = await dank.store_daily_gifts.reward_item(gift)
            if reward_item is not None:
                print(f"Item reward: {reward_item.name}")

        result = await dank.lottery.latest()
        if result is not None:
            print(f"Latest lottery: {result.drawn_at}, winnings {result.winnings:,}")

        print(f"Trending game: {await dank.stream.trending_game()}")


if __name__ == "__main__":
    asyncio.run(main())
