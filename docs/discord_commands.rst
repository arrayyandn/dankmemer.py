Slash command recipes
=====================

These complete extensions use the Bot from :doc:`cogs`. Save them alongside
``mybot/bot.py`` and load them before the Bot starts. Publish their commands
manually using :ref:`discord-command-syncing`.
The SDK client is ``bot.dank`` in Cogs and ``dank`` in function examples;
Discord's bot is ``bot``. Install the Discord extra from :doc:`installing`.

Items, autocomplete, and skins
------------------------------

This extension provides ``/item`` with prices and description, ``/search_items``
with up to ten approximate matches, and ``/item_skins`` for related skins.

.. literalinclude:: ../examples/items_cog.py
   :language: python
   :caption: mybot/items_cog.py

``ItemAutocomplete.create`` preloads the catalog once. Choices and lookups reuse
it, including when another Cog prepares its own helper. Selected choices carry
item IDs; typed names resolve exactly. See :doc:`autocomplete` for controls.

Commands defer before doing lookups, limit displayed results, and disable
mentions. The Cog's error handler provides useful responses for ambiguous names
and timeouts, and logs other SDK failures for the bot operator. Unexpected
programming errors propagate. Autocomplete errors produce no suggestions using
its own response type; autocomplete cannot defer like a normal command.

Fishing catalogs and active events
----------------------------------

This extension provides ``/creatures`` for a location, ``/creature`` with
related locations, tools, and a UTC time window, and ``/fishing_events``
excluding premium-only ones.

.. literalinclude:: ../examples/fishing_cog.py
   :language: python
   :caption: mybot/fishing_cog.py

Location and creature commands use reference catalogs, cached for one hour by
default. Relationship helpers resolve IDs through those catalogs. The active
events command reads current data when invoked; it creates no polling demand.
Catalog membership does not calculate current catchability. Fishing time bounds
remain available as raw values. ``/creature`` uses
``get_availability_window()`` to display the window starting on today's UTC
date; see :ref:`fishing-availability` for overnight windows and reversal.

Gifts, sales, lottery, merchant, and streaming
----------------------------------------------

This extension provides:

* ``/gifts`` — today's resolved reward text.
* ``/sales`` — current item sales and their end times.
* ``/lottery`` — the latest completed drawing, winnings, and entries.
* ``/merchant`` — autocomplete an item and find offers requiring it.
* ``/trending`` — the game returned by the streaming endpoint.
* ``/ban_status`` — check a Discord user's integer ID and reply privately.

.. literalinclude:: ../examples/activities_cog.py
   :language: python
   :caption: mybot/activities_cog.py

``resolve_reward`` returns a catalog model for supported item or bait rewards,
or ``None`` for other types. The original reward exposes its type and optional
quantity. An absent lottery drawing is ``None``; an HTTP failure is an exception.

Live routes have no retained SDK cache by default. Commands normally request
them when invoked and share the client's request pacing and allowance. The
60-second per-resource spacing applies to automatic polling and its retries;
manual commands can request the same resource sooner. A manual request can
postpone that resource's next automatic attempt.

For frequently used commands, cache live responses briefly so several users
do not cause repeated requests for the same data. For example, configure the
client in your launcher:

.. code-block:: python

   from datetime import timedelta
   from dankmemer import CacheConfig, DankMemer, TTLCache

   live_cache = TTLCache(ttl=timedelta(minutes=1))
   dank = DankMemer(
       token,
       cache=CacheConfig(
           lottery=live_cache,
           merchant_trades=live_cache,
           store_daily_gifts=live_cache,
           store_sales=live_cache,
           stream_trending_game=live_cache,
           fishing_events=live_cache,
       ),
   )

This reuses a manually fetched response for one minute. Event polls bypass
the SDK cache and keep their own schedule. Defer an interaction before a
potentially waiting request. :doc:`resources` and :doc:`events` explain API
caching and history limits.

Streaming responses contain no date proving when the game rotated. The example
displays the returned value without claiming that it has changed today.

Add a command to a function-based bot
-------------------------------------

Add this inside ``main`` from :ref:`discord-functions`, before ``bot.start``:

.. code-block:: python

   @bot.tree.command(description="Show the latest completed lottery drawing")
   async def lottery(interaction: discord.Interaction[commands.Bot]) -> None:
       await interaction.response.defer(thinking=True)
       result = await dank.lottery.latest()
       if result is None:
           await interaction.followup.send("No completed drawing is available.")
           return
       await interaction.followup.send(
           f"Latest winnings: {result.winnings:,}; entries: {result.total_entries:,}"
       )

The launcher starts and closes the SDK. Sync command definitions manually
using :ref:`discord-command-syncing`. See
:doc:`notifications` for messages driven by events instead of command invocations.
