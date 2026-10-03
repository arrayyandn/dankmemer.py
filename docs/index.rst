dankmemer.py
============

An asynchronous Python library for the official Dank Memer API.

Use dankmemer.py to find items by name, explore fishing data, check today's
gifts, and receive notifications when the data changes. It works with
discord.py or in a standalone Python application.

Features
--------

* Typed, read-only models with convenient attributes and UTC timestamps.
* Name and ID lookups, searches, filters, and related-record helpers.
* Configurable caching, including a one-hour default for item values.
* Item autocomplete choices for discord.py slash commands.
* Asynchronous pagination for catalogs, blogs, and changelogs.
* Event listeners with polling only for the resources you subscribe to.
* Optional persistent callback delivery and shared polling between processes.

Getting started
---------------

Python 3.11 or newer and a Dank Memer developer token are required.

Start with :doc:`installing` and :doc:`quickstart`. The :doc:`examples`
page helps you choose an example for a standalone application or a Discord bot.

For a particular method or return type, use the API reference below,
the :ref:`genindex`, or :ref:`search`.

.. toctree::
   :maxdepth: 1
   :caption: Getting started

   installing
   quickstart
   examples

.. toctree::
   :maxdepth: 1
   :caption: Guides

   resources
   discord
   cogs
   autocomplete
   discord_commands
   notifications
   standalone
   events
   advanced
   troubleshooting

.. toctree::
   :maxdepth: 1
   :caption: Persistent delivery

   delivery
   coordination

.. toctree::
   :maxdepth: 1
   :caption: API reference

   api/client
   api/resources
   api/events
   api/discord
   api/models
   api/configuration
   api/storage
   api/exceptions

.. toctree::
   :maxdepth: 1
   :caption: Releases

   migrating
   changelog
