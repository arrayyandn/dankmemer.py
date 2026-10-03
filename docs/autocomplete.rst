.. _discord-autocomplete:

Item autocomplete
=================

Use :class:`~dankmemer.ext.dpy.ItemAutocomplete` to add item-name
suggestions to a discord.py slash command. It returns Discord choices
directly and looks up the item selected by the user.

Add an item command
-------------------

The following function registers a slash command on an existing ``bot``.
Call ``await add_item_command(bot, dank)`` from its startup hook. Publish the
command through a separate manual sync, as described in
:ref:`discord-command-syncing`:

.. code-block:: python

   import logging

   import discord
   from discord import app_commands
   from discord.ext import commands

   from dankmemer import DankMemer, DankMemerError
   from dankmemer.ext.dpy import ItemAutocomplete

   async def add_item_command(bot: commands.Bot, dank: DankMemer) -> None:
       suggestions = await ItemAutocomplete.create(dank.items)

       @bot.tree.command(description="Find a Dank Memer item's market value")
       async def price(
           interaction: discord.Interaction[commands.Bot], name: str
       ) -> None:
           await interaction.response.defer(thinking=True)
           item = await suggestions.resolve(name)
           if item is None:
               await interaction.followup.send("Item not found.", ephemeral=True)
               return
           await interaction.followup.send(
               f"{item.name}: market value {item.market_value:,}",
               allowed_mentions=discord.AllowedMentions.none(),
           )

       @price.autocomplete("name")
       async def price_autocomplete(
           interaction: discord.Interaction[commands.Bot], current: str
       ) -> list[app_commands.Choice[str]]:
           try:
               return await suggestions.choices(current)
           except DankMemerError:
               logging.getLogger(__name__).exception("Item autocomplete lookup failed")
               return []

``current`` is the text being typed in the command's ``name`` field.
``choices`` returns up to 25 matches with item names as labels.
Choice values identify the item, so selecting a suggestion works even when
names are duplicated. ``resolve`` accepts those values or a full name typed
without selecting a suggestion.

For a complete runnable bot, use :ref:`discord-functions`. The
:doc:`cogs` example places both callbacks inside a Cog.

Loading and caching
-------------------

``ItemAutocomplete.create`` uses ``preload=True`` by default: startup loads
every item page. Suggestions and normal item lookups share the same catalog,
with a one-hour TTL by default. There is no request for each keystroke while
that catalog remains fresh.

``choices`` fetches a missing or expired catalog by default. If loading
takes more than two seconds, it returns no choices for that interaction;
the shared load can still complete for later callers. Autocomplete interactions
cannot defer their response. SDK request errors are raised to the autocomplete
callback. Catch them there and return ``[]``, as above, so Discord receives an
empty choice list. Discord's normal command error handler does not handle
autocomplete failures.

The slash command defers its response before resolving the selection,
which gives the regular lookup time to refresh an expired catalog.

Search and cache controls
-------------------------

Empty input suggests names alphabetically. Other input matches name substrings;
pass ``fuzzy=True`` to allow spelling mistakes:

.. code-block:: python

   return await suggestions.choices(current, fuzzy=True)

For applications that require autocomplete to make no requests:

.. code-block:: python

   suggestions = await ItemAutocomplete.create(dank.items, preload=False)
   cached_choices = await suggestions.choices(current, fetch=False)

That combination starts without loading anything and returns no choices until
another item lookup populates the cache. Omit ``preload=False`` to load it
during startup. An expired or refreshing catalog also returns no cached-only
choices. Keep a TTL cache enabled; ``NoCache()`` retains no suggestions.

Use the normal resource cache controls in :ref:`resource-caching` to change
the TTL or request a refresh. The helper creates no periodic refresh task.

See :doc:`api/discord` for method signatures and discord.py's
`autocomplete reference
<https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.Command.autocomplete>`_
for the callback contract.
