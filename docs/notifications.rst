Discord notifications
=====================

Slash commands read data when invoked. SDK listeners receive detected changes
while your application runs. Only resources required by listeners are polled.

A notification Cog
------------------

This extension demonstrates all ten polling resources. Save it alongside the
Bot from :doc:`cogs`:

.. literalinclude:: ../examples/alerts_cog.py
   :language: python
   :caption: mybot/alerts_cog.py

``@dank_cog.listen`` lets other Cogs receive the same events. ``bind`` connects
methods to ``bot.dank`` at load; ``unbind`` removes them at unload. Keep the
handlers your application needs: this whole example requests ten resources,
while just the three drop handlers request drops only, through one poller.
Default schedules in :ref:`polling-schedules` remain at least 60 seconds apart.

Drop start and end describe observed membership in the active collection.
``drop_ended`` receives the last observed record and can arrive after its end
time. Local timers do not manufacture ended events. ``drop_updated`` receives
the previous and current records for an ID. To notify only on end-time changes,
test ``before.ends_at != after.ends_at`` in that handler.

Lottery notifications follow drawing timestamps. Hourly polling continues with
minute-spaced checks when a result is delayed. Merchant and gift identities use
their reported UTC day. Publication handlers recover new entries ahead of their
checkpoint and deliver oldest first. Streaming uses the observed UTC day because
the endpoint supplies no date. For same-day streaming changes, also register
``stream_trending_game_updated`` and choose interval polling. See :doc:`api/events`.

Run commands and notifications together
---------------------------------------

Save the three command Cogs from :doc:`discord_commands` alongside ``alerts_cog.py``.
Replace ``mybot/launcher.py`` with:

.. literalinclude:: ../examples/multi_cog_launcher.py
   :language: python
   :caption: mybot/launcher.py

Run ``python -m mybot.launcher``. Extensions load before the Bot starts the SDK
and connects to Discord. Publish slash commands manually using
:ref:`discord-command-syncing`. All four share a client and item catalog.
The notification Cog owns polling demand; command Cogs read when invoked.
This launcher replaces
the introductory ``DankCog`` to avoid overlapping commands and notifications.

Notifications at startup
------------------------

The first successful read normally remembers existing data. To announce it too:

.. code-block:: python

   from dankmemer import DankMemer, EventConfig

   dank = DankMemer(token, events=EventConfig(emit_initial=True))

Active collections can announce several records on the first read. Lottery and
merchant handlers can announce the currently returned result. Update and ended
events need an earlier accepted record. See :doc:`events` for publication behaviour.

Sending failures and durable replay
-----------------------------------

Wait for Discord readiness inside callbacks. Ensure the notification channel
exists and the bot can view and send there. The example raises for an inaccessible
channel; Discord send failures also propagate to the SDK's delivery handling.

Best-effort failures are logged and consumed. Durable failures keep the saved
call and pause its subscription. Correct the problem, inspect
``last_delivery_errors``, then call ``dank.retry_pending(subscription_id)``.
:doc:`delivery` provides a complete persistent Discord launcher.

Durable delivery can repeat a message after a crash between sending and saving
completion. A subscription ID maps pending work to a handler; it does not make a
Discord send and database acknowledgement atomic. An application delivery record
can help track actions, but must also account for that crash window.

Normal function decorators
--------------------------

Inside the :ref:`function-based launcher <discord-functions>`, add:

.. code-block:: python

   from dankmemer import LotteryResult

   @dank.event
   async def on_lottery_result(result: LotteryResult) -> None:
       await bot.wait_until_ready()
       channel = bot.get_channel(alert_channel_id)
       if isinstance(channel, (discord.TextChannel, discord.Thread)):
           await channel.send(f"Latest lottery winnings: {result.winnings:,}")

For another consumer, use ``@dank.listen("lottery_result")``. Cog methods use
``dank_cog``; normal functions use the client ``dank``. See
:ref:`cog-primary-handlers` for replacement behaviour and
:ref:`subscription-identities` for durable consumer names.
