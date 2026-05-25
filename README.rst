dankmemer.py
============

``dankmemer.py`` is a lightweight asynchronous Python wrapper for the
`DankAlert API <https://api.dankalert.xyz/dank>`_. It provides typed response
objects, built-in caching, route-level filtering, and optional client-side
rate-limit protection.

Release Status
--------------

This package is currently prepared as ``1.0.0rc2``. The public API is usable
across the supported DankAlert route groups, but small fixes may still land
before a stable ``1.0.0`` release.

Installation
------------

Python 3.11 or newer is required.

Both package aliases are available:

.. code-block:: bash

    pip install dankmemer
    pip install dankmemer.py

Supported Routes
----------------

The client currently exposes these route groups:

- ``client.all``
- ``client.baits``
- ``client.buckets``
- ``client.creatures``
- ``client.decorations``
- ``client.events``
- ``client.items``
- ``client.locations``
- ``client.npcs``
- ``client.seasons``
- ``client.skills``
- ``client.skillsdata``
- ``client.stream``
- ``client.tanks``
- ``client.tools``

Features
--------

- Async ``aiohttp`` client with context-manager and manual-close usage
- Built-in caching with configurable TTL
- Cache clearing and cache state introspection
- Configurable retry handling for rate limits, temporary server errors, timeouts, and connection errors
- Configurable logging modes with default coloured console logs, silent logging, inherited application logging, or a custom logger
- Exact, fuzzy, membership, numeric range, above, and below filters
- Immutable dataclass response objects for route data
- Optional anti-rate-limit handling

Quick Start
-----------

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient, Fuzzy, IN, ItemsFilter, NPCsFilter

    async def main():
        async with DankMemerClient() as client:
            items = await client.items.query()
            print(items[0].name)

            filtered_items = await client.items.query(
                ItemsFilter(name=Fuzzy("trash", cutoff=80))
            )
            print([item.name for item in filtered_items])

            npcs = await client.npcs.query(NPCsFilter(name=IN("chad")))
            print([npc.name for npc in npcs])

    asyncio.run(main())

Stream Example
--------------

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient

    async def main():
        async with DankMemerClient() as client:
            stream = await client.stream.query()
            print(stream.trending_game.name)

    asyncio.run(main())

Manual Client Close
-------------------

.. code-block:: python

    import asyncio
    from dankmemer import DankMemerClient

    async def main():
        client = DankMemerClient()
        try:
            items = await client.items.query()
            print(len(items))
        finally:
            await client.close()

    asyncio.run(main())

Cache Control
-------------

Responses are cached by route for 24 hours by default. Set
``cache_ttl_hours=0`` or ``cache_ttl_hours=None`` to disable caching.

.. code-block:: python

    async with DankMemerClient(cache_ttl_hours=0) as client:
        first = await client.items.query()
        second = await client.items.query()

    async with DankMemerClient() as client:
        await client.items.query()
        client.items.clear_cache()
        client.clear_route_cache("items")
        client.clear_cache()
        print(client.items.cache_info())

Retries, Logging, and Sessions
------------------------------

The client retries HTTP 429 responses and selected temporary server errors by
default. ``Retry-After`` headers are respected when present.

.. code-block:: python

    client = DankMemerClient(
        retry_attempts=2,
        retry_backoff=0.5,
        retry_on_rate_limit=True,
    )

``DankMemerClient`` configures logging when a client is created. The default is
``logging_mode="default"``, which installs the package's coloured console
handler on the ``dankmemer`` logger. The package does not attach this handler
at import time and it is not duplicated when multiple clients are created.

Use ``logging_mode="null"`` for silent logging, or
``logging_mode="inherit"`` to leave output handling to your application or root
logging configuration. Pass ``logger=...`` when your application wants direct
control over client logs; a custom logger is used as-is.

.. code-block:: python

    import logging

    default_client = DankMemerClient()
    explicit_default = DankMemerClient(logging_mode="default")
    silent_client = DankMemerClient(logging_mode="null")
    inherited_client = DankMemerClient(logging_mode="inherit")

    api_logger = logging.getLogger("myapp.dankmemer")
    custom_client = DankMemerClient(logger=api_logger)

If you pass an existing ``aiohttp.ClientSession``, the client will not close it.
Sessions created by ``DankMemerClient`` are closed by ``close()`` and by the
async context manager.

Documentation
-------------

Full documentation is under development and will be published at:

https://dankmemerpy.readthedocs.io

Issues and contributions are welcome on GitHub.
