Events and polling
==================

SDK events describe changes between successful API reads. The API does not
push events to your application.

Watch for drops
---------------

Set your token as described in :doc:`installing`, save this as
``launcher.py``, and run ``python launcher.py``:

.. literalinclude:: ../examples/events.py
   :language: python
   :caption: launcher.py

This prints drops already available on startup and later drops that appear.
Only drops are polled. Press Ctrl+C to stop.
For a Discord notification handler, see :doc:`cogs`.
For multiple notification types in one extension, use :doc:`notifications`.

Register and remove handlers
----------------------------

``@dank.event`` uses the function's ``on_<event>`` name. It replaces any
previous primary handler for that event. Additional listeners can coexist:

.. code-block:: python

   @dank.listen("drop_started")
   async def log_drop(drop: Drop) -> None:
       print("Observed", drop.id)

   dank.remove_listener(log_drop, name="drop_started")

For a bound method or listener you need to remove later:

.. code-block:: python

   listener_id = dank.add_listener(callback, name="drop_started")
   dank.remove_listener(listener_id)

Every callback is ``async def`` and returns ``None``.
Names passed to ``listen`` or ``add_listener`` may include or omit
``on_``. See :doc:`cogs` for listener lifetime in a Discord extension.
Regular-function examples name the API client ``dank``. Cog methods use the
separate ``dank_cog`` decorators and bind them to ``bot.dank`` when the Cog loads.
See :ref:`subscription-identities` before choosing durable consumer IDs.

.. _polling-schedules:

Which requests run
------------------

Registering a drop listener creates demand for drops only.
Three listeners for drops share one polling worker. An unneeded resource makes
no automatic requests. Polling begins when you call ``await dank.start()``
or enter its async context manager. Removing the last listener stops future
polls for that resource.

``dank.active_polling_resources`` shows this demand, even before startup.
It includes resources paused after a request failure; inspect
``dank.paused_polling_resources`` to identify those resources.
Event polls bypass SDK data caches, while sharing the client's API allowance
tracking with manual requests.

.. list-table:: Default polling schedules
   :header-rows: 1
   :widths: 35 65

   * - Resource
     - Schedule while subscribed
   * - Drops, global boosts, store sales, fishing events
     - Every 60 seconds
   * - Blogs, changelogs
     - Every hour; unfinished history recovery continues at 60-second intervals
   * - Lottery
     - 15 seconds after each UTC hour; late results retry every minute for
       five minutes, then every five minutes
   * - Merchant trades, daily gifts
     - 15 seconds after UTC midnight; late data retries every minute for
       five minutes, then every 15 minutes
   * - Streaming game
     - 15 seconds after UTC midnight; the day is inferred from observation time

The first needed read runs at startup, subject to configured jitter and API
allowances. Every automatic request attempt to the same resource, including
retries and publication recovery pages, remains **at least 60 seconds apart**.
A recent manual request can postpone an automatic attempt.

After the current drawing or day's data arrives, a calendar schedule waits
for the next boundary. Same-period corrections are discovered on the next
scheduled read. Choose an interval policy if your application also needs
to watch for corrections throughout that period.

.. code-block:: python

   from dankmemer import DisabledPolling, HourlyPolling, IntervalPolling, PollingConfig

   polling = PollingConfig(
       blogs=IntervalPolling(hours=24),
       changelogs=IntervalPolling(hours=24),
       lottery=HourlyPolling(retry_window_minutes=10),
       store_daily_gifts=IntervalPolling(minutes=5),
       global_boosts=DisabledPolling(),
   )

Pass ``polling=polling`` to the client. An omitted or ``None`` field uses
the default. ``DisabledPolling()`` prevents automatic requests even with
listeners; already saved durable callbacks can still replay.

First reads and missed changes
------------------------------

When there is no previous baseline, the first successful read normally
remembers the current state without calling handlers. To also deliver the
state already available, as in the example above:

.. code-block:: python

   from dankmemer import EventConfig

   dank = DankMemer(token, events=EventConfig(emit_initial=True))

``emit_initial=True`` calls handlers for initial records; ``False`` remembers
them silently. Both settings still fetch the resource and report later changes.
With a persistent store, the SDK restores its saved baseline and compares
against it. Restarting does not make those records initial again. Pending
durable callbacks can replay regardless of ``emit_initial``.

Active records then emit their started handlers and aggregate changed handlers
with an empty ``before`` tuple. Lottery, merchant, gifts, and streaming emit
their ordinary result handlers. Publications emit entries from the first page,
rather than replaying all older history. Empty or null first responses do not
prevent a later arrival from producing an event.

Later reads are compared with the previous accepted read. A failed or
incomplete response keeps that previous state, so a broken response cannot
appear as every record ending. Internal storage calls this accepted state
a baseline and calls one complete parsed read an observation.

The API's 15-second response cache is a freshness duration, not event retention.
Drops, boosts, sales, and fishing events expose active records only; merchant
trades expose today's rotation, and lottery exposes the latest completed
drawing. A record that appears and disappears between reads cannot be recovered
through these routes. Only blogs and changelogs provide publication history.

Durable delivery preserves changes the SDK has already observed. It cannot
recover a change the API no longer exposes. Follow the API's polling guidance
rather than increasing polling to chase unobservable history.

Polling failures and retries
----------------------------

Failed polls retain the last accepted baseline and appear in
``dank.last_poll_errors``. Transient failures allow another scheduled poll.
HTTP 400, 401, and 403 pause automatic polling for that resource because the
request must be corrected before retrying. Listener additions, removals, or
Cog reloads do not clear the pause. Saved durable callbacks can still replay.

Check the recorded error and correct the cause. For example, after fixing a
key's permissions or IP allowlist in the dashboard:

.. code-block:: python

   from dankmemer import PollingResource

   if PollingResource.DROPS in dank.paused_polling_resources:
       dank.retry_polling(PollingResource.DROPS)

The client must be running. The retry waits for request spacing and allowance;
it does not send an immediate request or clear the previous error. A successful
poll clears that resource's error; another HTTP 400, 401, or 403 pauses it again.
Do not call this repeatedly without correcting the cause. If the token itself
is invalid or revoked, close the client and create one with a valid token.

In shared mode, polling pauses apply to the client that received the error.
Other clients can still poll with their own API access. This is separate from
durable callback pauses, which use ``retry_pending(subscription_id)``.

Callback names and arguments
----------------------------

The :doc:`api/events` reference lists every callback and its arguments,
grouped by resource. For example, ``drop_updated`` receives both the
previous and current :class:`~dankmemer.Drop`; ``drop_ended`` receives
the last record seen before its ID disappeared.

Delivery choices
----------------

.. list-table::
   :header-rows: 1
   :widths: 25 38 37

   * - Choice
     - Use it when
     - Effect
   * - ``BEST_EFFORT``, default
     - Occasional notifications can be lost on shutdown
     - Memory storage; callback failures are logged and discarded
   * - ``DURABLE``
     - Detected changes must survive a restart
     - Persistent store; failed calls remain pending and pause their subscription

Callbacks for one listener run sequentially in admitted order. Different
listeners run independently; there is no global callback completion order.
A durable call is acknowledged only after the handler returns successfully.
If the process stops between the external action and its acknowledgement,
the handler can receive the same call again. **Durable delivery is not
exactly once.** Make actions safe to repeat using an application-level identity
such as a drop ID, publication ID, or drawing time.

Stable subscription IDs, storage setup, and retries are explained in
:doc:`delivery`. For several worker processes, see :doc:`coordination`.

Bounds and monitoring
---------------------

``EventConfig`` defaults to 1,000 pending listener calls, publication pages of
100 records, and 1,000 staged records per publication resource.
One event sent to three listeners counts as three pending calls.
These bounds control work and memory; they do not describe API history or quotas.

If a detected batch cannot fit, it waits for space without being replaced
by newer data. Recovery and parsing errors retain the previous accepted read.
Check the following in your application's monitoring:

.. code-block:: python

   resources = dank.active_polling_resources
   pending = await dank.count_pending_events()
   poll_errors = dank.last_poll_errors
   paused = dank.paused_event_subscriptions
   delivery_errors = dank.last_delivery_errors

Successful cycles clear their resource's last poll error. Poll and callback
errors are also reported through the configured logger.
