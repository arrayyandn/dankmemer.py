Release notes
=============

1.0.0
-----

This release replaces the previous API client with an asynchronous SDK for
the official Dank Memer API. A developer token and Python 3.11 or newer are
required. Existing applications should follow :doc:`migrating`.

Resources and models
~~~~~~~~~~~~~~~~~~~~

* Item, pet, skin, and command catalogs with name lookups, ID lookups, searches,
  and filters. Item values are cached for one hour by default.
* Fishing creature, location, tool, bait, bucket, NPC, and skill catalogs,
  with helpers for related creatures, variants, pets, skins, and rewards.
* Creature availability windows as aware UTC datetimes, with reversed,
  full-day, and overnight bounds and an optional date to calculate for.
* Global boosts, store sales, drops, merchant trades, lottery results,
  daily gifts, active fishing events, and the streaming game's current name.
* Blogs and changelogs with asynchronous pagination.
* Ban status lookups using integer Discord user IDs.
* Read-only typed models, snake_case attributes, UTC timestamps, and
  explicit errors for invalid required response fields.
* Per-resource TTL policies, cache clearing, refresh-on-demand, and shared
  in-progress catalog loads.
* Typed autocomplete helpers with default preloading, fetch-on-demand,
  cached-only searches, and up to 25 suggestions.

Events and delivery
~~~~~~~~~~~~~~~~~~~

* Twenty-four event signatures covering all ten polling resources.
* Polling only for resources needed by registered listeners, with a minimum
  interval of 60 seconds and configurable interval or UTC calendar schedules.
* Hourly lottery polling with minute-spaced checks while a drawing is delayed;
  daily merchant, gift, and streaming schedules; hourly publication polling.
* Primary event decorators, additional listener decorators, and explicit
  listener registration and removal.
* Optional discord.py Cog decorators with explicit binding and cleanup hooks,
  inherited-method support, signature checks, and reload-safe listener ownership.
* Best-effort delivery in memory, or durable callback replay using SQLite,
  PostgreSQL, or an application-supplied ``EventStore`` implementation.
* Stable subscription IDs, saved baselines, pending-callback diagnostics,
  failure pauses, and explicit retries.
* Shared polling and callback ownership for multiple processes using the same
  official API application.

Lifecycle and requests
~~~~~~~~~~~~~~~~~~~~~~

* Lazy HTTP session setup and explicit async startup.
* Optional shutdown grace periods for running manual requests and callbacks.
  Pending durable calls stay saved for replay.
* Caller ownership of supplied HTTP sessions and event stores.
* Request allowance tracking from response headers and Retry-After, bounded
  retry backoff, per-attempt timeouts, and total request deadlines.
* Standard Python logging, optional custom loggers, and a silent option.

Documentation
~~~~~~~~~~~~~

* Standalone examples and discord.py slash commands with autocomplete, using
  functions, Bot subclasses, and reloadable Cogs.
* Separate command and notification Cogs, SQLite and PostgreSQL Discord setup,
  command-error handling, and practical catalog and pagination recipes.
* Every event's meaning, callback arguments, initial behavior, and polling
  schedule.
* Guides for caching, explicit startup, persistent storage, shared workers,
  migration, and troubleshooting.

Limits to account for
~~~~~~~~~~~~~~~~~~~~~

Most event routes expose active records or the current period's data rather
than an event history. A record that appears and disappears between polls
can be missed. Durable replay preserves callbacks detected by the library;
it cannot recover changes the API no longer exposes.

Streaming data has no date or revision timestamp, so a late change cannot
reliably be attributed to a particular UTC day. Fishing time bounds retain
the API's numeric values; the availability helper interprets them as UTC hours
using the previous package's reversal convention.
See :doc:`events` and :doc:`resources` for these constraints.
