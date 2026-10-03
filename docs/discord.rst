Using discord.py
================

Use one Dank Memer client for your bot. Slash commands look up data when
invoked, and Dank Memer listeners receive changes while the bot is running.

Install the Discord extra from :doc:`installing`. Invite your bot with
the ``bot`` and ``applications.commands`` scopes. Notification channels need
View Channel and Send Messages permissions; slash commands do not require
Message Content Intent.

Choose an example
-----------------

* :ref:`discord-functions` — one file with commands and event decorators.
* :doc:`cogs` — a Bot that owns the client and a Cog that owns commands
  and notifications.
* :doc:`autocomplete` — add item suggestions to an existing slash command.
* :doc:`discord_commands` — item, skin, fishing, merchant, lottery, and other commands.
* :doc:`notifications` — several Cogs sharing one client and event-driven alerts.

.. _discord-functions:

Functions and decorators
------------------------

This complete example provides ``/item`` with name autocomplete and sends
daily gift notifications. Set ``DANK_MEMER_API_TOKEN``,
``DISCORD_BOT_TOKEN``, and ``ALERT_CHANNEL_ID`` as described in
:doc:`installing`.

Save it as ``launcher.py`` and run ``python launcher.py``:

.. literalinclude:: ../examples/discord_functions.py
   :language: python
   :caption: launcher.py

``@bot.tree.command()`` registers a Discord command. ``@dank.event``
registers a Dank Memer callback. The item helper loads its catalog before
Discord login and shares the normal one-hour item cache.

Registering a command adds it to the Bot's local command tree. Publish it to
Discord using the manual steps in :ref:`discord-command-syncing`.

``dank`` is the ``DankMemer`` instance, and ``bot`` is Discord's Bot.
For additional consumers of one event, use ``@dank.listen("event_name")``.
Cog methods use ``@dank_cog.event`` or ``@dank_cog.listen(...)`` from
``dankmemer.ext.dpy`` and the binding hooks described in :doc:`cogs`.

With no saved baseline, the first successful gift read records today's gifts
without sending a notification. To notify for gifts already available, pass
``events=EventConfig(emit_initial=True)`` to ``DankMemer`` and import
``EventConfig`` from ``dankmemer``. ``False``, the default, skips those initial
notifications. Later changes are reported with either setting.

.. _discord-subclass:

A Bot subclass
--------------

The Bot owns the SDK and its lifecycle. Commands and notification handlers
belong to the Cog in :doc:`cogs`.

.. literalinclude:: ../examples/bot.py
   :language: python
   :caption: mybot/bot.py

Load the Cog before ``bot.start()``, as shown in its launcher.
``setup_hook`` starts event polling. ``close`` closes the SDK and then Discord.

.. important::

   Wait for Discord readiness inside notification callbacks. Waiting in
   ``setup_hook`` prevents the websocket from connecting. Avoid starting
   the SDK or syncing commands in ``on_ready``, which can run again after
   a reconnect.

.. _discord-command-syncing:

Command syncing
---------------

Discord keeps registered slash commands between bot restarts. Sync manually
when you add, change, or remove command definitions. Repeating the sync on
every startup sends unnecessary requests to Discord.

After the bot has logged in and all command Cogs are loaded, run this from
your owner-only admin tooling or a trusted async console:

.. code-block:: python

   await bot.tree.sync()

This publishes the current global command tree, including removals. Load all
your command Cogs first so the tree contains every command you want to publish.

For testing in one server, manually copy and sync the commands to that server:

.. code-block:: python

   guild = discord.Object(id=YOUR_SERVER_ID)
   bot.tree.copy_global_to(guild=guild)
   await bot.tree.sync(guild=guild)

Use ``self.tree`` inside a Bot method. Keep syncing as an explicit admin action;
do not place it in ``setup_hook`` or ``on_ready``.

For polling behavior, see :doc:`events`. To preserve detected notifications
across restarts, see :doc:`delivery`.
