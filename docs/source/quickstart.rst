Quick Start
===========

Below is a minimal example to help you get started with dankmemer.py.
Python 3.11 or newer is required.

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient, ItemsFilter, NPCsFilter

    async def main():
        async with DankMemerClient(cache_ttl_hours=24) as client:
            # Query all items without filtering.
            all_items = await client.items.query()
            print("All items:", all_items)

            # Query NPCs without filtering.
            all_npcs = await client.npcs.query()
            print("All NPCs:", all_npcs)

    asyncio.run(main())

This example shows the basic usage of DankMemerClient and how to perform queries.

Cache and Retry Configuration
-----------------------------

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient

    async def main():
        async with DankMemerClient(
            cache_ttl_hours=0,
            retry_attempts=2,
            retry_backoff=0.5,
        ) as client:
            items = await client.items.query()
            print(len(items))
            print(client.items.cache_info())

    asyncio.run(main())

Using Multiple Filters
------------------------------------------

The following example demonstrates how to use multiple filters simultaneously.
It shows how you can combine membership matching, fuzzy matching, and numeric filters.

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient, ItemsFilter, NPCsFilter, Fuzzy, IN, Above, Range

    async def main():
        async with DankMemerClient() as client:
            # Query items using multiple filters:
            # - Name must contain either "sword" or "dagger".
            # - Market value must be above 5000.
            # - Type must exactly match "Weapon".
            items_filter = ItemsFilter(
                name=IN("sword", "dagger"),
                marketValue=Above(5000),
                type="Weapon"
            )
            filtered_items = await client.items.query(items_filter)
            print("Filtered Items (multiple filters):", [item.name for item in filtered_items])

            # Query NPCs using multiple filters:
            # - Name must fuzzy match "chad" with a cutoff of 75.
            # - Reputation must be within the range 10 to 100.
            npcs_filter = NPCsFilter(
                name=Fuzzy("chad", cutoff=75),
                reputation=Range(10, 100)
            )
            filtered_npcs = await client.npcs.query(npcs_filter)
            print("Filtered NPCs (multiple filters):", [npc.name for npc in filtered_npcs])

    asyncio.run(main())

This example demonstrates:

- **Membership Matching:** 
    using the IN interface to check if the item's name contains "sword" or "dagger".

- **Numeric Filtering:**
    using Above and Range to filter on numeric fields.

- **Fuzzy Matching:**
    filtering NPC names with a fuzzy matching approach.
