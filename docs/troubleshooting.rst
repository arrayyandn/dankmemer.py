Troubleshooting
===============

No event arrived
----------------

Check that you registered the handler, started the client, and kept it open.
The first successful read remembers the current state unless ``emit_initial=True``.
Then check ``active_polling_resources``, ``paused_polling_resources``, ``last_poll_errors``,
``paused_event_subscriptions``, and ``last_delivery_errors``.

A daily or hourly calendar schedule waits for its next boundary after receiving
the current period's result. An event can also be missed if its active record
disappeared between polls. See :doc:`events` for schedules and retention limits.

A Discord command does not respond
----------------------------------

Invite the bot with the ``applications.commands`` scope, load its Cogs,
and manually sync the command tree after changing command definitions, as
described in :ref:`discord-command-syncing`. Global commands can
take time to appear; use a guild sync while testing. Slash commands do not
require Message Content Intent.

Defer the command response before an API request that may take time.
Autocomplete cannot defer its response. Preload suggestions at startup; the
helper also bounds its wait when a refresh is needed. See :ref:`discord-autocomplete`.
SDK listeners are registered on ``bot.dank``; Discord Cog listeners alone
do not register them.

No Discord notification was sent on startup
-------------------------------------------

By default the SDK reports later changes only. Set
``events=EventConfig(emit_initial=True)`` if existing records should also
produce notifications. Verify ``ALERT_CHANNEL_ID`` and ensure your bot can
view and send to that channel.

A lookup returned None
----------------------

Name lookup is exact after case and whitespace normalization.
Use ``search`` to find candidates or ``get_by_id`` if you know the ID.
Publication ID lookups only search the configured ``scan_limit``.
An ambiguous name raises an error instead of choosing a record silently.

Values look out of date
-----------------------

Reference catalogs, including item values, use a one-hour SDK cache by default.
Use ``refresh=True`` or choose a shorter TTL. The API can also serve its
own cached response; refreshing the SDK does not bypass the API's cache.

An HTTP 404 or response error appeared
--------------------------------------

``NotFound`` is an HTTP failure, not an empty collection.
``DankMemerResponseError`` indicates that a required field differs from
the expected contract; inspect its ``path`` and ``field``.
Check the official docs and library version before catching and discarding
such failures.

API access was rejected
-----------------------

HTTP 401 raises ``AuthenticationError``. Check that the token is valid;
create a new client if you replace the token. HTTP 403 raises ``Forbidden``.
Check application approval, key permissions, your host's outbound public IP,
and any origin configured for the key. :doc:`installing` explains these settings.
HTTP 400 raises ``BadRequest``; inspect the request arguments and API contract.

These errors pause automatic polling for the affected resource. After fixing
the cause, call ``retry_polling`` with its ``PollingResource`` member while
the client is running. A listener or Cog reload does not clear this pause.
The last accepted baseline remains available for comparison when polling resumes.

A durable callback was repeated
-------------------------------

A handler can succeed just before a crash prevents its acknowledgement from
saving. The saved call is replayed after restart. This also applies to shared
mode. Use an application-level idempotency strategy for external actions.
See :doc:`delivery`.

A durable subscription stays paused
-----------------------------------

Fix the error shown in ``last_delivery_errors``, then call
``retry_pending`` with the stable subscription ID while the client is
running. Shared failure pauses persist across restart and extension reload.
Inspect the pending count if a removed or offline consumer has filled the queue.

Shutdown and caller-owned resources
-----------------------------------

Keep caller-owned stores and sessions open until SDK shutdown completes.
The default ``close()`` cancels unfinished work. Use a positive
``grace_seconds`` to let running manual requests and handlers finish before
cancellation. New resource calls are rejected once closing begins.
A handler that initiates closing can finish and save its acknowledgement
after the client closes, so its store must still be open.
Do not wait for Discord readiness inside its ``setup_hook``.
