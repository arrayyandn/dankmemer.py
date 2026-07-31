dankmemer.py
============

``dankmemer.py`` is a lightweight asynchronous Python wrapper for Dank Memer
item data. Version ``1.0.0rc3`` uses the limited `Gwapes items API
<https://api.gwapes.com/items>`_ and provides typed response objects, built-in
caching, filtering, and optional client-side rate-limit protection.

`Read the full documentation <https://dankmemerpy.readthedocs.io/en/latest/>`_.

Important Service and Rules Notice
----------------------------------

.. warning::

   On 25 July 2026, Dank Memer announced Rule 13, `External Bots and
   Services <https://dankmemer.lol/rules>`_. The rule prohibits developing or
   using services that scrape Dank Memer's API, website, or Discord responses,
   and is due to be enforced from 1 September 2026.

   The third-party DankAlert API previously used by this package has shut down
   permanently, including its ``/items`` endpoint. Version ``1.0.0rc3`` no
   longer relies on DankAlert and supports only item data through Gwapes.

   If documentation for Dank Memer's `official API
   <https://dankmemer.lol/api>`_ becomes available, the intention is to migrate
   this package to use only that API and support its available features. A
   successfully migrated release would no longer rely on the prohibited
   third-party service. This migration is not guaranteed because the maintainer
   must first be approved for API access in order to test, develop, and complete
   it. No release date has been announced beyond the stated Q4 2026 target.

Release Status
--------------

This package is currently prepared as ``1.0.0rc3``. With the default client,
only ``client.items`` is supported.

Installation
------------

Python 3.11 or newer is required.

Both package aliases are available:

.. code-block:: bash

    pip install dankmemer
    pip install dankmemer.py

Supported Routes
----------------

The default client supports only ``client.items`` through Gwapes. The existing
route attributes remain present for API compatibility, but calling any other
route raises ``UnsupportedRouteException`` before an HTTP request is made.

Callers that explicitly provide a non-Gwapes ``base_url`` retain the legacy
route request behaviour for compatible custom servers. Those servers are not
officially supported or guaranteed by this package.

Gwapes supplies fewer fields than the former DankAlert endpoint:

.. list-table:: Item field availability
   :header-rows: 1

   * - Existing ``Item`` attribute
     - Gwapes source
   * - ``name``
     - ``name``
   * - ``imageURL``
     - ``attachment``
   * - ``marketValue``
     - ``value``
   * - ``netValue``
     - ``net_value``
   * - ``rarity`` and ``type``
     - Split from the combined Gwapes ``type`` value
   * - ``id``, ``details``, ``emoji``, ``flavor``, ``hasUse``, ``itemKey``,
       ``skins``, ``tags``, and ``value``
     - Unavailable and returned as ``None``

The following legacy route attributes are unavailable with the default client:

- ``client.all``
- ``client.baits``
- ``client.buckets``
- ``client.creatures``
- ``client.decorations``
- ``client.events``
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
    from dankmemer import DankMemerClient, Fuzzy, ItemsFilter

    async def main():
        async with DankMemerClient() as client:
            items = await client.items.query()
            print(items[0].name)

            filtered_items = await client.items.query(
                ItemsFilter(name=Fuzzy("trash", cutoff=80))
            )
            print([item.name for item in filtered_items])

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

https://dankmemerpy.readthedocs.io/en/latest/

Issues and contributions are welcome on GitHub.
