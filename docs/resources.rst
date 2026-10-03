Working with resources
======================

The client groups methods by the data you need: ``dank.items``,
``dank.fishing``, ``dank.store_daily_gifts``, and so on. A resource method
makes requests when you call it. Using a resource method does not subscribe
to events or start background polling.

Items, names, and search
------------------------

.. code-block:: python

   item = await dank.items.get("Life Saver")
   matches = await dank.items.search("life", max_limit=5)
   suggestions = await dank.items.search("life savr", fuzzy=True, max_limit=5)
   affordable = await dank.items.filter(max_market_value=50_000)

``get`` matches a complete name, ignoring case and surrounding whitespace.
It returns ``None`` for a missing name and raises
:class:`~dankmemer.AmbiguousLookupError` for duplicate names.
``search`` finds substrings; ``fuzzy=True`` explicitly enables approximate
matching. A lookup never chooses an approximate match for you.

If you already know the ID or key:

.. code-block:: python

   item = await dank.items.get_by_id(158)
   item = await dank.items.get_by_key("lifeSaver")

IDs are integers for items, drops, and sales. Pet, skin, fishing, and publication
IDs are strings. User IDs are positive Python integers. Command IDs are strings
when Discord has loaded them; a :class:`~dankmemer.Command` may have
``id=None``, so name lookup is useful for commands too.

All reference catalogs provide ``get``, ``get_by_id``,
``get_many_by_id``, ``search``, ``fetch``, and ``iter``.
Items and skins additionally support keys. Skin names can be narrowed by
``type`` and ``reference``; fishing skill names by ``tier``.
See :doc:`api/resources` for every method's signature.

.. _resource-caching:

Caching and changing values
---------------------------

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - Resources
     - Default SDK cache
   * - Items, pets, skins, commands, all seven fishing catalogs
     - One hour
   * - Blogs, changelogs, and all live resources
     - No retained cache
   * - User ban status
     - Always requested

The first name lookup loads the complete catalog, including all its pages.
ID lookups, name lookups, searches, and filters share that loaded catalog.
An item's values therefore update together with the rest of the item record.
Use ``refresh=True`` when you need a fresh read:

.. code-block:: python

   item = await dank.items.get("Life Saver", refresh=True)

To change the policy for your application:

.. code-block:: python

   from datetime import timedelta
   from dankmemer import CacheConfig, DankMemer, NoCache, TTLCache

   dank = DankMemer(
       token,
       cache=CacheConfig(
           items=TTLCache(ttl=timedelta(minutes=10)),
           pets=NoCache(),
           store_daily_gifts=TTLCache(ttl=timedelta(minutes=5)),
       ),
   )

These TTLs control the SDK's local cache, separately from the API's own caching.
Expiry is checked on access; it does not cause background requests.
``max_entries`` bounds cached entries per resource: a complete catalog is
one entry regardless of how many items it contains; separately fetched pages
each occupy one entry.

Concurrent requests for the same data share one load, including with
``NoCache()``. A failed reload raises its error; expired data is not silently
returned. Clear caches without requesting data with
``dank.items.clear_cache()``, ``dank.fishing.clear_cache()``, or
``dank.clear_cache()``.

Fishing and related records
---------------------------

``dank.fishing`` exposes ``creatures``, ``locations``, ``npcs``,
``tools``, ``baits``, ``buckets``, and ``skills``.

.. code-block:: python

   creatures = await dank.fishing.creatures_at("River")
   if creatures:
       locations = await dank.fishing.resolve_locations(creatures[0])
       tools = await dank.fishing.resolve_tools(creatures[0])

   location = await dank.fishing.locations.get("River")
   if location is not None:
       npcs = await dank.fishing.resolve_npcs(location)

Other relationship helpers include ``dank.skins.for_item(item)``,
``dank.skins.for_pet(pet)``, ``dank.pets.friendly_to(pet)``, and
``dank.fishing.skills.prerequisite(skill)``. They reuse the relevant
catalogs. A missing named starting record produces an empty relationship tuple;
a missing record referenced by an existing model raises a response error.

Catalog filters describe the catalog data. For example, matching a creature's
location does not prove that it is currently catchable.

.. _fishing-availability:

Creature availability windows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use :meth:`~dankmemer.FishingCreature.get_availability_window` to convert a
creature's time bounds into start and end datetimes:

.. code-block:: python

   from datetime import UTC, datetime

   creature = await dank.fishing.creatures.get("Example fish")
   if creature is not None:
       starts_at, ends_at = creature.get_availability_window()
       print(f"{starts_at:%Y-%m-%d %H:%M} to {ends_at:%Y-%m-%d %H:%M} UTC")
       within_time_window = starts_at <= datetime.now(UTC) < ends_at

The helper interprets the API's numeric bounds as UTC hours. It uses the same
reversal convention as the previous package: ``reversed=True`` swaps the start
and end. The API reference describes these fields as numeric bounds; these
time conversions are the library's interpretation.

The start date defaults to today's UTC date. Pass a calendar date explicitly
when calculating another day's window:

.. code-block:: python

   from datetime import date

   if creature is not None:
       starts_at, ends_at = creature.get_availability_window(day=date(2026, 10, 3))

.. list-table::
   :header-rows: 1
   :widths: 15 15 20 50

   * - Start
     - End
     - Reversed
     - Returned window
   * - 0
     - 24
     - Either
     - 00:00 on the chosen day to 00:00 the next day
   * - 8
     - 20
     - False or omitted
     - 08:00 to 20:00 on the chosen day
   * - 8
     - 20
     - True
     - 20:00 on the chosen day to 08:00 the next day
   * - 20
     - 8
     - False or omitted
     - 20:00 on the chosen day to 08:00 the next day

The start is inclusive and the end is exclusive. Outside the full-day case,
24 represents midnight, and equal bounds produce a zero-length window.
Bounds outside 0 to 24 raise ``ValueError``. Raw values remain available on
``creature.time``. The helper makes no HTTP requests.

An overnight window starts on the chosen day. At 02:00 UTC, a 20:00–08:00
window calculated for today begins at 20:00 tonight. To check its overnight
portion from yesterday, calculate yesterday's window using ``day``.
This checks the creature's time window; location days, disabled locations,
and other game requirements still apply.

Drops, sales, gifts, and fishing events
---------------------------------------

These routes return the current collection without pagination. Their filters
are applied locally after reading the collection:

.. code-block:: python

   drops = await dank.drops.active(patreon_only=False, partner_only=False)
   sales = await dank.store_sales.active(subject_kind="item")
   gifts = await dank.store_daily_gifts.today(category="fish")
   fishing_events = await dank.fishing_events.active(premium_only=False)

``active`` preserves the API's active collection; it does not invent past or
future records. ``get(id)`` on each resource searches its current collection
and returns ``None`` for an absent ID. For gifts, ``get(pool_id)`` uses the
string pool ID supplied by the API.

The convenience methods connect live data to catalog records:

.. code-block:: python

   item = await dank.items.get("Life Saver")
   if item is not None:
       sales = await dank.store_sales.for_item(item)

   for gift in await dank.store_daily_gifts.today():
       print(gift.metadata.render)
       reward_item = await dank.store_daily_gifts.reward_item(gift)
       if reward_item is not None:
           print(reward_item.name)

``reward_item`` returns ``None`` for a reward type it cannot resolve as an
item. An item reward with a missing ID or absent catalog reference raises
:class:`~dankmemer.DankMemerResponseError`. The complete reward remains
available through ``gift.reward.data``, including unfamiliar fields.

Models with start and end times provide ``is_active`` and
``time_remaining`` without making requests. Gift models use ``day`` and
``expires_at`` for these properties.

Other current data
------------------

.. code-block:: python

   boosts = await dank.global_boosts.active()
   result = await dank.lottery.latest()
   rotation = await dank.merchant_trades.today()
   game = await dank.stream.trending_game()
   banned = await dank.users.is_banned(123456789012345678)

Lottery and merchant reads return ``None`` when the API explicitly reports
null data. An HTTP failure is an exception, even if its status is 404.
Unknown user IDs return ``False`` according to the API contract.

``dank.merchant_trades.for_item("Life Saver")`` finds offers that require
the named item. ``resolve_cost(trade)`` and ``resolve_reward(trade)``
resolve supported reward references to their catalog models.

Publications
------------

Blogs and changelogs retain published history:

.. code-block:: python

   latest = await dank.blogs.latest()
   matches = await dank.blogs.search("fishing", scan_limit=200, max_limit=10)
   changelog = await dank.changelogs.get_by_id(entry_id, scan_limit=200)

``scan_limit`` bounds how many recent entries are examined.
``max_limit`` bounds how many matches are returned. A missing ID means it
was not found within that scan. Set ``scan_limit=None`` explicitly to
traverse all available history.

Pagination
----------

Use ``fetch`` for one page or ``iter`` for an asynchronous iterator:

.. code-block:: python

   page = await dank.blogs.fetch(page_size=25)
   if page.next_cursor is not None:
       next_page = await dank.blogs.fetch(page_size=25, cursor=page.next_cursor)

   async for blog in dank.blogs.iter(page_size=25, max_limit=100):
       print(blog.title, blog.url)

``page_size`` is 1–100. ``iter`` yields at most 100 unique records by
default; use ``max_limit=None`` for all available pages, or 0 for no
requests. Cursors are offsets into the current collection. Concurrent changes
can shift these offsets: overlapping IDs are deduplicated, but complete coverage
cannot be guaranteed while the collection changes. Commands are deduplicated
by name because their Discord IDs can be absent.

Models and failures
-------------------

Returned models are read-only dataclasses with snake_case attributes.
Collections are tuples or read-only mappings; dated timestamps are aware UTC
``datetime`` objects. Model properties never make API requests.
Required fields with an unexpected type raise
:class:`~dankmemer.DankMemerResponseError`. Its ``path`` and ``field``
identify the failed response field.

.. code-block:: python

   from dankmemer import DankMemerHTTPError, DankMemerResponseError

   try:
       result = await dank.lottery.latest()
   except DankMemerHTTPError as error:
       print(error.status_code, error.path)
   except DankMemerResponseError as error:
       print(error.path, error.field)

An empty tuple or ``None`` describes a successful response with no matching
data. It does not hide a network or parsing failure.

For Discord slash command suggestions, see :doc:`autocomplete`.
