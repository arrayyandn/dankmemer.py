Discord integration
===================

Install ``dankmemer[discord]`` to use this optional module. Importing the
ordinary ``dankmemer`` client does not import discord.py.

See :doc:`../autocomplete` for a slash command using the helper and
:doc:`../cogs` for its lifetime in an extension.

Cog decorators and lifetime
---------------------------

Import ``dank_cog`` from ``dankmemer.ext.dpy``. These decorators mark Cog
methods; register them in ``cog_load`` with ``bind`` and remove them in
``cog_unload`` with ``unbind``. Normal functions use the API client's
``@dank.event`` and ``@dank.listen(...)`` decorators.

.. autofunction:: dankmemer.ext.dpy.dank_cog.event

.. autofunction:: dankmemer.ext.dpy.dank_cog.listen

.. autofunction:: dankmemer.ext.dpy.dank_cog.bind

.. autofunction:: dankmemer.ext.dpy.dank_cog.unbind

Item autocomplete
-----------------

.. autoclass:: dankmemer.ext.dpy.ItemAutocomplete()
   :members: create, choices, resolve
