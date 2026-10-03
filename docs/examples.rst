Examples
========

Choose the example that matches the application you want to build.
Set your token as described in :doc:`installing` before running any example.

Standalone applications
-----------------------

* :doc:`quickstart` — look up an item and read its market value.
* :ref:`standalone-resources` — read daily gifts, find related items, check sales,
  and inspect the latest lottery result and streaming game.
* :ref:`standalone-events` — keep a client open and receive drop notifications.
* :doc:`standalone` — complete catalog, relationship, cache-refresh, and pagination recipes.

Discord bots
------------

* :ref:`discord-functions` — use commands and listeners defined as functions.
* :ref:`discord-subclass` — let a Bot subclass own client startup and shutdown.
* :doc:`cogs` — put commands and notifications in a reloadable Cog.
* :doc:`autocomplete` — suggest item names in a slash command.
* :doc:`discord_commands` — several ready-to-load command Cogs with practical lookups.
* :doc:`notifications` — separate command and notification Cogs sharing one client.
* :ref:`discord-durable-delivery` — a persistent Discord launcher using SQLite or PostgreSQL.

Configuration and delivery
--------------------------

* :ref:`resource-caching` — change item caching and refresh values on demand.
* :ref:`polling-schedules` — choose how often subscribed resources are polled.
* :doc:`advanced` — control client startup, timeouts, and logging.
* :doc:`delivery` — preserve detected callbacks with SQLite or PostgreSQL.
* :doc:`coordination` — share polling across processes.

.. _standalone-resources:

Use several resources together
------------------------------

Save this as ``launcher.py`` and run ``python launcher.py``:

.. literalinclude:: ../examples/standalone.py
   :language: python
   :caption: launcher.py

The item lookup and gift reward helper share the item catalog cache.
Live resources are requested when you call their methods. The client closes
after the program finishes.

.. _standalone-events:

Watch drops without a Discord bot
---------------------------------

The :doc:`events` guide includes a complete drop notifier, its polling
schedule, and how to include drops already available at startup.
Every callback is documented in :doc:`api/events`.
