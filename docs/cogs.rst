Using Cogs
==========

A Cog groups your slash commands and notification handlers. Keep one Dank
Memer client on the Bot so every Cog shares its session, catalog cache,
request allowance, and event polling. The Bot handles startup and shutdown;
commands and notification handlers belong to the Cogs.

This walkthrough provides ``/price`` with item autocomplete, ``/gifts``,
and drop notifications. It uses ordinary ``commands.Cog`` classes.

Create your project
-------------------

Install ``dankmemer[discord]`` and configure the tokens and notification
channel as described in :doc:`installing`. Then create:

.. code-block:: text

   your_project/
       mybot/
           __init__.py
           launcher.py
           bot.py
           dank_cog.py

Leave ``__init__.py`` empty. Save this as ``mybot/bot.py``:

.. literalinclude:: ../examples/bot.py
   :language: python
   :caption: mybot/bot.py

The launcher supplies a configured ``DankMemer`` instance. ``setup_hook``
starts subscribed polling. ``close`` closes the SDK before Discord.
Supplying the client also lets you configure caching
or persistent delivery in the launcher.

Commands and decorated listeners
--------------------------------

Save this as ``mybot/dank_cog.py``:

.. literalinclude:: ../examples/dank_cog.py
   :language: python
   :caption: mybot/dank_cog.py

``@app_commands.command()`` creates a Discord slash command.
``@price.autocomplete("name")`` provides item-name suggestions.
``@dank_cog.listen(...)`` marks a Dank Memer callback on the Cog.
``@commands.Cog.listener()`` continues to handle Discord's own events.

``cog_load`` prepares item suggestions, then ``dank_cog.bind(self, self.bot.dank)``
registers the marked methods with ``self`` bound to this Cog instance.
``cog_unload`` calls ``dank_cog.unbind(self)`` to remove its registrations.
Both helpers are synchronous; they register callbacks without fetching data.
If the SDK is running, new demand starts polling subject to its schedule.

The item helper preloads the catalog. Commands and autocomplete share its
one-hour cache. The drop handler waits for Discord readiness before sending.
Waiting in ``setup_hook`` would prevent Discord from connecting.

Load and run the extension
--------------------------

Save this as ``mybot/launcher.py``:

.. literalinclude:: ../examples/launcher.py
   :language: python
   :caption: mybot/launcher.py

Run this from ``your_project``:

.. code-block:: console

   python -m mybot.launcher

Before using the slash commands, follow :ref:`discord-command-syncing` to
publish their definitions manually. Then try ``/price`` and select a suggestion
or enter an exact name. ``/gifts`` fetches gifts when invoked. Only drops are
automatically polled here.
The first successful poll remembers existing drops without notifying you.
To announce existing drops too, pass ``events=EventConfig(emit_initial=True)``
when constructing ``DankMemer``. Import ``EventConfig`` from ``dankmemer``.

.. _cog-primary-handlers:

Primary handlers and additional listeners
-----------------------------------------

Use ``listen`` when independent Cogs should receive the same event. They get
separate delivery queues and share one resource poller. The method name selects
the event when ``listen()`` has no name argument:

.. code-block:: python

   @dank_cog.listen(subscription_id="alerts.drop_started")
   async def on_drop_started(self, drop: Drop) -> None:
       await self.send_alert(f"Drop {drop.id} is available.")

Use ``event`` for the client's single primary handler. For example:

.. code-block:: python

   import discord
   from discord.ext import commands
   from dankmemer.ext.dpy import dank_cog
   from .bot import DankBot

   class StreamingCog(commands.Cog):
       def __init__(self, bot: DankBot) -> None:
           self.bot = bot

       async def cog_load(self) -> None:
           dank_cog.bind(self, self.bot.dank)

       async def cog_unload(self) -> None:
           dank_cog.unbind(self)

       @dank_cog.event
       async def on_stream_trending_game(self, game: str) -> None:
           await self.bot.wait_until_ready()
           channel = self.bot.get_channel(self.bot.alert_channel_id)
           if isinstance(channel, (discord.TextChannel, discord.Thread)):
               await channel.send(
                   f"Current streaming game: {game}",
                   allowed_mentions=discord.AllowedMentions.none(),
               )

   async def setup(bot: DankBot) -> None:
       cog = StreamingCog(bot)
       try:
           await bot.add_cog(cog)
       except BaseException:
           dank_cog.unbind(cog)
           raise

Save it as ``mybot/streaming_cog.py`` and load ``mybot.streaming_cog`` in
your launcher to use it.

Binding another primary handler replaces the old primary on ``bot.dank``,
including one from another Cog. Additional listeners remain registered.
Unloading the new Cog does not restore the old handler. Unloading the old Cog
cannot remove its replacement.

Decoration marks methods without registering them. ``bind`` checks all methods
before changing registrations, including their event arguments and subscription
IDs. Inherited marked methods are included; overriding one without a decorator
leaves it unregistered. Static and class methods are unsupported.

.. _cog-explicit-listeners:

Explicit registration and removal
---------------------------------

You can register a bound method directly. This complete alternative uses the
listener ID returned by ``add_listener``:

.. literalinclude:: ../examples/explicit_cog.py
   :language: python
   :caption: mybot/explicit_cog.py

Load ``mybot.explicit_cog`` instead of ``mybot.dank_cog`` to try it. It adds
drop notifications only. Alternatively, remove the callback with
``bot.dank.remove_listener(cog.on_drop_started, name="drop_started")``.
Register each callback through one approach so its lifetime has one owner.

.. _subscription-identities:

What subscription IDs identify
------------------------------

A ``subscription_id`` names one notification consumer. Choose a string such
as ``"alerts.drop_started"``. It is not a Discord ID or a value sent to the
API. It identifies the consumer rather than an individual drop.

With durable delivery, detecting a drop saves a pending call for that ID.
If the application stops before successful completion, registering the same
ID after restart connects the saved call to the replacement handler.

Two handlers watching drops might use ``"discord.drop_alerts"`` and
``"audit.drop_log"``. They have separate completion tracking, so one can finish
while the other still needs to retry. Give different consumers and events
distinct IDs within the same store. Keep an ID unchanged across restarts,
method renames, and Cog reloads. A new random ID on each load would leave
previous calls without their intended handler.

In-memory ``listen`` can omit the ID. Durable ``listen`` and ``add_listener``
require an explicit stable ID. ``event`` supplies ``on_<event>`` by default;
an explicit ID keeps consumer identity independent of its Python method name.

Reloading and load failures
---------------------------

Reload from your bot's admin tooling:

.. code-block:: python

   await bot.reload_extension("mybot.dank_cog")

The old Cog unbinds before its replacement binds. Commands are removed and
added again locally. Sync manually if their definitions changed, as described
in :ref:`discord-command-syncing`. Removing the last listener for a resource
stops future polls, while an in-progress request can finish.
The SDK remains available to other Cogs.

Repeated ``bind`` calls for the same Cog and client have no effect. Repeated
``unbind`` calls and cleanup after SDK shutdown are safe. Unbind before moving
a Cog to another client.

Finish other startup work before binding. If your own code fails after binding
inside ``cog_load``, clean up before propagating the error:

.. code-block:: python

   async def cog_load(self) -> None:
       self.suggestions = await ItemAutocomplete.create(self.bot.dank.items)
       dank_cog.bind(self, self.bot.dank)
       try:
           await self.finish_setup()
       except BaseException:
           dank_cog.unbind(self)
           raise

Discord can also reject ``add_cog`` after ``cog_load`` has finished, for example
when a slash command name conflicts with an existing command. The extension's
``setup`` keeps the Cog instance and unbinds it if installation fails. Keep
that guard when adding commands or notification handlers. With explicit
listener registration, call ``await cog.cog_unload()`` in the failure guard.

Do not rely on ``cog_unload`` to clean up every failed installation. Unbinding
discards pending in-memory calls; durable calls remain saved for the same
subscription ID. See :doc:`delivery` for replay and retries.

Growing into several Cogs
-------------------------

:doc:`discord_commands` splits item, fishing, and current-data commands into
separate extensions. :doc:`notifications` supplies a notification Cog and a
launcher loading all four. They share ``bot.dank``. Choose the commands and
handlers your application needs.
