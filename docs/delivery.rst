Persistent event delivery
=========================

Use durable delivery when detected notifications need to survive a restart.
A callback is saved before it runs and acknowledged after it returns.
It can run again after a crash; use an event ID or drawing time to make
external actions safe to repeat.

Durable replay with SQLite
--------------------------

Install the SQLite extra from :doc:`installing`, then give the client a
persistent store and stable subscription IDs. Save this complete example as
``watch_drops.py`` and run ``python watch_drops.py``. It also supplies its own
HTTP session and application identity:

.. literalinclude:: ../examples/advanced.py
   :language: python
   :caption: watch_drops.py

``AsyncExitStack`` closes resources in reverse order: client first, store
second, HTTP session last. The client closes only sessions it creates and
never closes a supplied event store. Registering the cleanup before
``start`` also covers a startup failure.

The listener uses ``drop-notifier.alerts`` across restarts. A new
independent client restores its saved baseline and replays unacknowledged
calls for registered IDs. Polling can be disabled while replay continues.
Callbacks that have already performed an external action can run again
if their acknowledgement was interrupted; make those actions safe to repeat.

Use one active independent client per persistent store. Separate applications
or independent workers should use separate stores.

.. _discord-durable-delivery:

Persistent Discord notifications
--------------------------------

Use the Bot from :doc:`cogs` and ``alerts_cog.py`` from :doc:`notifications`.
Install both extras with
``python -m pip install --upgrade "dankmemer.py[discord,sqlite]>=1.0.0"``. Replace
``mybot/launcher.py`` with this complete launcher:

.. literalinclude:: ../examples/durable_launcher.py
   :language: python
   :caption: mybot/launcher.py

The Bot starts the configured client in ``setup_hook``. Its ``close`` method
closes the SDK before extensions unload, and ``dank_cog.unbind`` safely handles
that ordering. The outer store context stays open until all Bot cleanup finishes.
Use ``python -m mybot.launcher`` as before.

Each marked listener has a stable subscription ID. Detected notifications are
saved before dispatch, and completed calls are acknowledged. After a restart,
the same IDs attach pending work to the replacement Cog. The saved baseline
prevents the unchanged current data from becoming a newly detected event.
Replay can still repeat a previously sent Discord message if its completion
was not saved; this is not a guarantee of exactly-once Discord delivery.

For PostgreSQL, install
``"dankmemer.py[discord,postgres]>=1.0.0"`` with the same pip options and replace the store
import and context with:

.. code-block:: python

   from dankmemer.storage.postgres import PostgresEventStore

   async with await PostgresEventStore.open(os.environ["DATABASE_URL"]) as store:
       dank = DankMemer(
           os.environ["DANK_MEMER_API_TOKEN"],
           events=EventConfig(delivery=EventDelivery.DURABLE),
           event_store=store,
       )
       bot = DankBot(dank, alert_channel_id=int(os.environ["ALERT_CHANNEL_ID"]))
       async with bot:
           await bot.load_extension("mybot.alerts_cog")
           await bot.start(os.environ["DISCORD_BOT_TOKEN"])

For independent delivery, run one active client per store. If several processes
share the same store and API application, configure :doc:`coordination`.

PostgreSQL and existing pools
-----------------------------

Install the PostgreSQL extra from :doc:`installing`. Set ``DATABASE_URL``
to your database connection URL, then replace the SQLite store in the
example above with:

.. code-block:: python

   from dankmemer.storage.postgres import PostgresEventStore

   store = await stack.enter_async_context(
       await PostgresEventStore.open(
           os.environ["DATABASE_URL"],
           schema="public",
       )
   )

The schema must already exist. The store creates only its own tables and
indexes there. ``open`` creates a pool owned by the store.
To share a pool owned by your application, use
``await PostgresEventStore.from_pool(pool, schema="public")``; closing that
store leaves your pool open.

Handling a failed callback
--------------------------

A durable callback failure preserves its call and pauses that subscription.
Inspect and fix the cause, then resume the stable subscription ID:

.. code-block:: python

   subscription_id = "drop-notifier.alerts"
   error = dank.last_delivery_errors.get(subscription_id)
   pending = await dank.count_pending_events(subscription_id=subscription_id)

   # Retry after the application has addressed the failure.
   dank.retry_pending(subscription_id)

Retrying saved callbacks makes no API request. A handler can still make its
own requests. Independent delivery also retries on a new client startup;
shared delivery persists a failure pause until an explicit retry.

Removing a durable listener keeps its pending records. Re-register the same
ID to finish them. Unregistered records still count against the pending-call
limit; the library does not silently expire them.

Custom event stores
-------------------

Implement :class:`~dankmemer.EventStore` to use another persistent backend.
It is a structural protocol: your class need not inherit from it, but must
provide its typed methods and atomicity guarantees.
Pass an instance with ``event_store=your_store``. It remains your
application's responsibility to open and close that store.
The :doc:`api/storage` reference specifies the required methods and how
saving detected changes, pending callbacks, and acknowledgements must behave.
For shared coordination, implement :class:`~dankmemer.CoordinatedEventStore`
as well.
