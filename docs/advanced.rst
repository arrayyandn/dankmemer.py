Client lifecycle and requests
=============================

Most applications should use one client in an ``async with`` block.
Use explicit startup when another framework owns your lifecycle:

.. code-block:: python

   dank = DankMemer(token)
   dank.add_listener(callback, name="drop_started")
   try:
       await dank.start()
       await run_application()
   finally:
       await dank.close(grace_seconds=10)

Constructing the client makes no requests. The first resource call can prepare
its HTTP session without starting event polling. ``start`` starts polling
for registered listeners. A started client stays bound to its event loop and
cannot be reopened after closing.

If entering ``async with dank`` fails, the client closes its owned resources
and propagates the startup error. Supplied sessions and stores remain yours
to close. With explicit ``start()``, keep cleanup in ``finally`` as above,
including when startup fails.

``close(grace_seconds=10)`` stops polling and rejects new resource calls,
then gives running manual requests and handlers up to ten seconds to finish.
It does not start queued callbacks. Shared ownership is renewed while a
running handler finishes, so another worker does not take over during the wait.
After the wait, unfinished work is cancelled. Cancellation and session cleanup
can take additional time.

The default ``close()``, ``grace_seconds=None``, and zero cancel unfinished
work immediately. Durable pending calls stay saved for replay; best-effort
pending calls are discarded. The first concurrent close call chooses the
grace period. A handler that itself calls ``close`` is excluded from the wait
and can finish after the client closes; keep its store open until it returns.

The :doc:`discord` examples place startup in ``setup_hook`` and shutdown in
the Bot's ``close`` method.

Request limits, timeouts, and logging
-------------------------------------

The initial admission defaults follow the documented 60 attempts per minute
and 10,000 per day for an application. Response headers update this allowance,
including developer-approved increases. ``Retry-After`` remains authoritative.
All keys belonging to an application share its server-side allowance, and an
individual key can have a lower limit. Minute windows reset at the next UTC
minute; daily windows reset at UTC midnight.
Requests made outside the SDK or outside a shared group can consume it too.

``RequestConfig.timeout_seconds`` bounds one network attempt.
``total_timeout_seconds`` also includes allowance waits, retries, and backoff.
For example, a manual request can have a 30-second total timeout while an
allowance reset is 60 seconds away. The request cannot finish before its
deadline, so it raises a timeout instead of waiting beyond that deadline.

Transient connection failures and retryable server responses use the configured
backoff; HTTP 503 has at least five seconds of base backoff. Authentication
failures and HTTP 404 are raised without a transport retry. Automatic polling
also pauses after HTTP 400, 401, or 403 until its cause is corrected and you
call ``retry_polling``. See :doc:`events` for recovery.

Application identity adds a name and optional version to the User-Agent.
It does not report or request higher quotas.

The SDK uses the standard ``dankmemer`` logger without configuring your
application's handlers:

.. code-block:: python

   import logging

   logging.basicConfig(level=logging.INFO)
   dank = DankMemer(token)
   custom = DankMemer(token, logger=logging.getLogger("mybot.api"))
   quiet = DankMemer(token, silent=True)

``silent=True`` suppresses SDK logging and cannot be combined with a custom
logger. It does not suppress exceptions raised to your application.

