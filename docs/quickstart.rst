Your first request
==================

Set your token as shown in :doc:`installing`, then save this as
``launcher.py``. You can run it without a Discord bot:

.. literalinclude:: ../examples/quickstart.py
   :language: python
   :caption: launcher.py

Run ``python launcher.py``. If the item exists, the program prints its name and
market value.

``async def`` defines an asynchronous function. ``await`` waits for the requested
data without blocking the rest of your application. ``asyncio.run(main())``
creates the event loop for this standalone program.

The ``async with`` block starts the client and closes its HTTP session when the
block ends, including when an exception is raised. Keep one client open for
your application's lifetime so requests can share connections and cached data.

Finding an item
---------------

``await dank.items.get("Life Saver")`` looks up an exact item name while ignoring
case and surrounding whitespace. It returns an :class:`~dankmemer.Item` or
``None`` when no matching name exists. Check for ``None`` before accessing its
fields. If more than one item has the name, the library raises
:class:`~dankmemer.AmbiguousLookupError` so you can choose an ID explicitly.

Name lookup loads the complete item catalog on its first use. Further lookups
reuse it for one hour by default. :doc:`resources` explains searches, filters,
refreshing values, and pagination.

Where to go next
----------------

* :ref:`standalone-resources` shows daily gifts, sales, and related item lookups.
* :ref:`standalone-events` keeps the client open to receive notifications.
* :doc:`discord` adds item commands and notifications to a Discord bot.
* :doc:`cogs` organizes commands and listeners in a reloadable extension.
* :doc:`advanced` covers explicit startup, timeouts, and logging.
* :doc:`delivery` preserves detected callbacks across restarts.
