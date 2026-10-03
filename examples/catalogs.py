import asyncio
import os

from dankmemer import DankMemer


async def main() -> None:
    async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
        matches = await dank.items.search("life savr", fuzzy=True, max_limit=5)
        for match in matches:
            print(match.name, match.market_value)
        affordable = await dank.items.filter(max_market_value=50_000)
        print("Affordable catalog items:", len(affordable))

        item = await dank.items.get("Life Saver")
        if item is not None:
            print("Skins:", [skin.name for skin in await dank.skins.for_item(item)])
            updated = await dank.items.get_by_id(item.id, refresh=True)
            if updated is not None:
                print("Refreshed value:", updated.market_value)

        pet = await dank.pets.get("Cat")
        if pet is not None:
            friends = await dank.pets.friendly_to(pet)
            print("Friendly pets:", [friend.name for friend in friends])

        location = await dank.fishing.locations.get("River")
        if location is not None:
            creatures = await dank.fishing.creatures_at(location)
            print("Creatures:", [creature.name for creature in creatures])
            print(
                "NPCs:", [npc.name for npc in await dank.fishing.resolve_npcs(location)]
            )


if __name__ == "__main__":
    asyncio.run(main())
