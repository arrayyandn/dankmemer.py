Migrating from rc3 to 1.0.0
===========================

Version 1.0.0 uses the official Dank Memer API and replaces the previous
client, route objects, filters, and models. Applications written for rc3 need
code changes. The import name remains ``dankmemer``.

Start with :doc:`installing` to upgrade and obtain an official developer token.
This guide maps the old interfaces to their replacements.

Create the new client
---------------------

Replace ``DankMemerClient()`` with ``DankMemer(token)``:

.. code-block:: python

   import asyncio
   import os

   from dankmemer import DankMemer


   async def main() -> None:
       async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
           item = await dank.items.get("Life Saver")
           if item is not None:
               print(item.name, item.market_value)


   asyncio.run(main())

There is no ``DankMemerClient`` alias or configurable ``base_url``.
The client uses the official API with your token. An optional ``origin``
sets the HTTP Origin header; it does not select an API server.

Constructing the client makes no requests. A resource call prepares its HTTP
session when needed; ``start()`` also starts event polling for registered
listeners. Use ``async with`` for automatic startup and cleanup, or follow
:doc:`advanced` when a framework owns your application's lifecycle.

Replace item queries and filter objects
---------------------------------------

Replace ``await client.items.query(ItemsFilter(...))`` with the operation
that matches your purpose:

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Purpose
     - 1.0.0
   * - Exact item name
     - ``await dank.items.get("Life Saver")``
   * - Numeric item ID
     - ``await dank.items.get_by_id(158)``
   * - Exact item key
     - ``await dank.items.get_by_key("lifesaver")``
   * - Name suggestions
     - ``await dank.items.search("life sav", fuzzy=True)``
   * - Market value bounds
     - ``await dank.items.filter(min_market_value=100, max_market_value=10_000)``
   * - One API page
     - ``await dank.items.fetch(page_size=100)``
   * - Iterate through the catalog
     - ``dank.items.iter(max_limit=None)`` in an ``async for`` loop

Name lookups ignore case and normalize whitespace. Missing matches return
``None``; duplicate exact names raise ``AmbiguousLookupError``.
String IDs belong in ``get_by_id`` for resources whose IDs are strings.
Discord user IDs passed to ``users.is_banned`` are positive integers.

The ``ItemsFilter``, ``Fuzzy``, ``IN``, ``Above``, ``Below``, and
``Range`` classes are removed. Filtering uses typed keyword arguments,
and fuzzy name search uses ``fuzzy=True`` with an optional ``min_score``.
The new filters cover the fields in the official models; they do not accept
every field from the old API.

Collection helpers such as ``filter`` and ``search`` return tuples.
``fetch`` returns a ``Page`` with ``items`` and ``next_cursor``.
``iter`` returns an async iterator and defaults to at most 100 unique records;
use ``max_limit=None`` for all available pages. See :doc:`resources`.

Update model attributes and imports
-----------------------------------

Import public models from ``dankmemer`` or ``dankmemer.models``.
The old ``dankmemer.routes`` modules are removed.

Models use snake_case attributes and read-only collections:

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - Old Item attribute
     - New Item attribute
   * - ``imageURL``
     - ``image_url``
   * - ``itemKey``
     - ``key``
   * - ``marketValue``
     - ``market_value``
   * - ``tags`` dictionary
     - Tuple of tag strings
   * - ``skins`` dictionary
     - Tuple of typed ``Skin`` models
   * - ``emoji``, ``hasUse``, ``netValue``, ``rarity``
     - Not present on the official Item model

``Item.sell_value`` is a separate value supplied by the official API.
It is not a renamed ``netValue``.
Do not assume that fields with similar names have the same meaning across APIs.

Nested data uses typed models instead of ``DotDict`` or a generic ``extra``
container. Dated timestamps are timezone-aware UTC ``datetime`` objects.
Required fields with an unexpected type raise ``DankMemerResponseError``
instead of silently becoming ``None``.
See :doc:`api/models` for each model's fields and optional values.

Move fishing resources under fishing
------------------------------------

Fishing catalogs now belong to ``dank.fishing``:

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - Old client attribute
     - 1.0.0
   * - ``client.creatures``
     - ``dank.fishing.creatures``
   * - ``client.locations``
     - ``dank.fishing.locations``
   * - ``client.tools``
     - ``dank.fishing.tools``
   * - ``client.baits``
     - ``dank.fishing.baits``
   * - ``client.buckets``
     - ``dank.fishing.buckets``
   * - ``client.npcs``
     - ``dank.fishing.npcs``
   * - ``client.skills``
     - ``dank.fishing.skills``
   * - ``client.events``
     - ``dank.fishing_events`` for currently active fishing events

The combined ``all`` endpoint and the old ``seasons``, ``decorations``,
``tanks``, and separate ``skillsdata`` route objects have no matching
1.0.0 resource. The official fishing skill model includes its published tiers.

``FishingCreature.get_availability_window()`` replaces the old creature helper
and returns aware UTC start and end datetimes. It preserves the previous
hour, reversal, and full-day conventions. Its optional ``day`` argument anchors
the window to a particular date. Raw numeric bounds remain on
``FishingCreature.time``; see :ref:`fishing-availability` for examples.
``fishing.creatures_at(...)`` finds catalog relationships; it does not
determine which creatures are available at the current time.

Change caching and request configuration
----------------------------------------

The old global ``cache_ttl_hours`` option is replaced by per-resource policies.
Reference catalogs, including items, default to one hour. Live resources and
publications default to no SDK caching.

.. code-block:: python

   from datetime import timedelta

   from dankmemer import CacheConfig, DankMemer, NoCache, TTLCache

   dank = DankMemer(
       token,
       cache=CacheConfig(
           items=TTLCache(ttl=timedelta(minutes=15)),
           pets=NoCache(),
       ),
   )

Use ``refresh=True`` to refresh a resource on demand, ``dank.items.clear_cache()``
to clear its cache, or ``dank.clear_cache()`` to clear every resource.
The old ``cache_info`` and ``clear_route_cache`` methods are removed.

Request and retry options are grouped into configuration objects:

.. code-block:: python

   from dankmemer import DankMemer, RequestConfig, RetryConfig

   dank = DankMemer(
       token,
       request=RequestConfig(timeout_seconds=30, total_timeout_seconds=90),
       retry=RetryConfig(max_attempts=3),
   )

``max_attempts`` includes the first request, as ``retry_attempts`` did.
The default is now three attempts, with five seconds of initial backoff.
The total timeout includes allowance waits, backoff, and network attempts.
``request_timeout`` and the flat retry arguments are removed.

``useAntirateLimit`` is removed. Requests always follow allowance tracking,
response headers, and Retry-After. Automatic event polls for a resource are
at least 60 seconds apart. See :doc:`advanced` for timeouts and allowances.

Update error handling and logging
---------------------------------

Import errors from ``dankmemer`` or ``dankmemer.errors`` instead of
``dankmemer.exceptions``:

.. list-table::
   :header-rows: 1
   :widths: 52 48

   * - rc3 exception
     - 1.0.0 exception
   * - ``DankMemerException``
     - ``DankMemerError``
   * - ``DankMemerHTTPException``
     - ``DankMemerHTTPError``
   * - ``DankMemerConnectionException``
     - ``DankMemerConnectionError``
   * - ``DankMemerResponseException``
     - ``DankMemerResponseError``
   * - ``NotFoundException``
     - ``NotFound``
   * - ``BadRequestException``
     - ``BadRequest``
   * - ``RateLimitException``
     - ``RateLimited``
   * - ``ServerErrorException``
     - ``ServerError``

``DankMemerResponseError`` is no longer a subclass of the HTTP error.
Catch ``DankMemerError`` for all library errors, or handle network, HTTP,
and parsing failures separately. HTTP failures expose ``status_code``
and ``path``; ``path`` replaces the old ``route`` attribute.
``UnsupportedRouteException`` has no replacement.

The library uses standard Python logging without installing a colored handler.
Configure your application's handlers yourself. Replace
``logging_mode="null"`` with ``silent=True``.
The default uses the ``dankmemer`` logger; a custom ``logger=...`` is still
supported and cannot be combined with silent mode.

Add notifications when you need them
------------------------------------

Event listeners are new in 1.0.0. Only subscribed resources are polled after
startup. By default the first observation records the current state without
sending callbacks. Use ``EventConfig(emit_initial=True)`` to receive that
initial state too.

Start with :doc:`events` for signatures, schedules, and retention limits.
Polling observes available snapshots; it cannot recover changes that
disappear from routes with no history between polls.

For a Discord bot, choose :doc:`discord` or :doc:`cogs`.
For delivery that survives a process restart, choose SQLite or PostgreSQL
with stable subscription IDs as shown in :doc:`delivery`.
