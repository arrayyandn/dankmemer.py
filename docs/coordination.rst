Shared workers
==============

Use shared coordination when several processes use the same API application.
For one bot process, the default independent mode needs no setup.
Configure :doc:`delivery` before enabling shared workers.

Independent or shared coordination
----------------------------------

Delivery mode decides whether detected callbacks survive a restart.
Coordination mode decides which process polls and delivers them.

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Mode
     - What happens
     - Choose it when
   * - ``INDEPENDENT``, default
     - Each client owns its polling and allowance tracking
     - One process runs your bot, or your application coordinates its workers
   * - ``SHARED``
     - Clients use a common database to elect one poller per resource and share
       allowance tracking
     - Several processes use the same Dank Memer API application

Shared mode requires durable delivery and a
:class:`~dankmemer.CoordinatedEventStore`. Both supplied database backends
implement this protocol.

.. code-block:: python

   from dankmemer import (
       CoordinationConfig, CoordinationMode, DankMemer, EventConfig, EventDelivery,
   )

   dank = DankMemer(
       token,
       events=EventConfig(delivery=EventDelivery.DURABLE),
       event_store=store,
       coordination=CoordinationConfig(
           mode=CoordinationMode.SHARED,
           application_id="your-public-dashboard-application-id",
       ),
   )

Each process opens its own store connection to the **same** SQLite file or
PostgreSQL schema. Use the same public dashboard application ID, even when
the processes use different keys belonging to that application.
One store is bound to one application. All participating clients must agree
on event settings and enabled polling policies.

Suppose two bot workers both register ``bot.drop_alerts``. They represent
one logical consumer, so one worker owns its callback delivery at a time.
If another service registers ``dashboard.drop_alerts``, it is a separate
consumer and also receives each detected change.

Each worker renews its ownership while it is running. After a crash, another
worker can take over once that ownership expires, normally within 30 seconds.
Coordination also checks the database for saved callbacks; these checks make
no API requests. Automatic API attempts still obey the 60-second minimum and
the resource's configured schedule.

A callback can be repeated if ownership changes after it sends a message but
before its success is saved. Make external actions safe to repeat, just as
with independent durable delivery.

Shared registrations and diagnostics
------------------------------------

Register listeners before startup where possible. Changes made while running
are saved in the background. To wait for them to reach the shared database:

.. code-block:: python

   await dank.sync_event_subscriptions()

This also waits for a queued ``retry_pending`` update.
``last_coordination_error`` reports renewal or background update failures.
A successful coordination operation clears it.
``active_polling_resources`` includes saved shared demand, so it does not
mean that the current process owns every listed polling worker.

Registrations remain enabled when a process closes or crashes: another worker
can continue saving changes for the offline consumer. Explicit listener
removal disables that saved ID once no live worker registers it.
An offline consumer's backlog can fill the pending limit and prevent new
observations from committing. Plan how to resume or retire consumers that
will no longer return.

