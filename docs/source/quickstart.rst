Quick Start
===========

Python 3.11 or newer is required. The default client supports item data through
the limited Gwapes API.

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient

    async def main():
        async with DankMemerClient(cache_ttl_hours=24) as client:
            items = await client.items.query()
            for item in items[:5]:
                print(item.name, item.marketValue, item.netValue)

    asyncio.run(main())

Limited Item Fields
-------------------

Gwapes does not provide every attribute on the existing ``Item`` object.
``name``, ``imageURL``, ``marketValue``, ``netValue``, ``rarity``, and ``type``
are populated when their source values are available. Other legacy attributes
are ``None``.

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
----------------------

Only filters backed by available Gwapes fields can match results.

.. code-block:: python

    import asyncio
    from dankmemer import Above, DankMemerClient, IN, ItemsFilter

    async def main():
        async with DankMemerClient() as client:
            item_filter = ItemsFilter(
                name=IN("sword", "dagger"),
                marketValue=Above(5000),
                type="Collectible",
            )
            items = await client.items.query(item_filter)
            print([item.name for item in items])

    asyncio.run(main())

Unavailable Routes
------------------

Routes other than ``items`` are unavailable with the default client and raise
``UnsupportedRouteException`` without making an HTTP request. Legacy behaviour
is retained only when an explicit non-Gwapes ``base_url`` points to a compatible
custom server.
