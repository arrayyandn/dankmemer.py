Installation
============

Requirements
------------

Use Python 3.11 or newer. You also need a developer token from the
`Dank Memer dashboard <https://dankmemer.lol/dashboard/developers>`_.

Install with pip
----------------

.. code-block:: console

   python -m pip install --upgrade "dankmemer.py>=1.0.0"

The package imports as ``dankmemer``. These guides use the official API client
introduced in version 1.0.0. Choose the documentation version that matches
your installed package when using the Read the Docs version selector.

The same releases are available under the mirror name ``dankmemer``. Install
one of the two package names in each environment; both import as ``dankmemer``.

Optional dependencies
---------------------

Ordinary requests and best-effort event listeners need no database.
For callbacks that survive a restart, choose a persistent store:

.. code-block:: console

   python -m pip install --upgrade "dankmemer.py[sqlite]>=1.0.0"
   python -m pip install --upgrade "dankmemer.py[postgres]>=1.0.0"

Install the SQLite extra for a local file, or the PostgreSQL extra to use a
PostgreSQL database. See :doc:`delivery` for setup and delivery guarantees.

For the Discord bot examples and item autocomplete helper, install:

.. code-block:: console

   python -m pip install --upgrade "dankmemer.py[discord]>=1.0.0"

This extra installs discord.py. Standalone applications do not need it.

Configure your token
--------------------

The examples read your API token from an environment variable named
``DANK_MEMER_API_TOKEN``.

In PowerShell:

.. code-block:: powershell

   $env:DANK_MEMER_API_TOKEN = "your Dank Memer developer token"

On Linux or macOS:

.. code-block:: sh

   export DANK_MEMER_API_TOKEN="your Dank Memer developer token"

You can use another variable name in your application and pass its value to
:class:`~dankmemer.DankMemer`. Keep the token private.

Using a .env file
~~~~~~~~~~~~~~~~~

The library does not read ``.env`` files itself. To use one, install
python-dotenv:

.. code-block:: console

   python -m pip install python-dotenv

Create a ``.env`` file in your application's directory:

.. code-block:: text

   DANK_MEMER_API_TOKEN=your Dank Memer developer token

Then load it before reading the environment:

.. code-block:: python

   import os
   from dotenv import load_dotenv

   load_dotenv()
   token = os.environ["DANK_MEMER_API_TOKEN"]

Discord bot settings
~~~~~~~~~~~~~~~~~~~~

The bot examples also read ``DISCORD_BOT_TOKEN`` and ``ALERT_CHANNEL_ID``.
Set them in the same way as your API token:

* ``DISCORD_BOT_TOKEN`` is your Discord bot's token.
* ``ALERT_CHANNEL_ID`` is the numeric ID of the channel for notifications.

The Discord bot token and Dank Memer API token belong to different services.
See :doc:`discord` for intents, channel permissions, and complete bot examples.

API access
----------

Before running an example, approve your application's required permissions in
the developer dashboard and enable them for its API key:

* ``StaticContent`` allows reference lookups, such as items and fishing catalogs.
* ``Events`` allows event resources, such as drops, lottery, and daily gifts.
  Manually fetching those resources also needs this permission.
* ``BannedUserData`` allows ``dank.users.is_banned(user_id)``.

The bot examples that combine item commands and notifications need both
``StaticContent`` and ``Events``. The ban-status command additionally needs
``BannedUserData``. Only request permissions your application uses.

Each key needs an IP allowlist containing between one and ten exact public
IP addresses. Add the outbound IP of the machine or hosting service running
your application. A private address such as ``127.0.0.1`` is not its public
outbound address. Wildcards and CIDR ranges are unsupported. Update the
allowlist if your outbound IP changes.

If you configured an allowed HTTPS origin for the key, pass that same origin
to the client:

.. code-block:: python

   from dankmemer import DankMemer

   dank = DankMemer(token, origin="https://example.com")

Otherwise omit ``origin``. A Discord bot does not automatically need an origin
just because it connects to Discord. If a request returns ``Forbidden``
(HTTP 403), check the application's approval, key permissions, IP allowlist,
and any configured origin before retrying.

Follow the `official API documentation
<https://dankmemer.lol/dashboard/developers/docs>`_, `terms
<https://dankmemer.lol/legal/terms>`_, and `privacy policy
<https://dankmemer.lol/legal/privacy>`_. Library settings do not grant additional
API permissions or change your application's server-side allowance.

