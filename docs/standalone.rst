Standalone recipes
==================

The package works without Discord. Configure ``DANK_MEMER_API_TOKEN`` from
:doc:`installing`, save any example below as ``launcher.py``, and run
``python launcher.py``. The async context manager closes the session on exit.
With no registered listeners, it makes no automatic event requests.

Search, filter, and resolve related catalogs
--------------------------------------------

.. literalinclude:: ../examples/catalogs.py
   :language: python
   :caption: launcher.py

Exact lookup returns ``None`` for unknown names. Searches return candidates;
fuzzy matching never silently changes an exact lookup. Filters use catalog data.
Values and relationship lookups share one-hour caches. ``refresh=True`` reloads
on demand. Example names such as ``Life Saver``, ``Cat``, and ``River`` may differ
from the live catalog; use ``search`` to discover names.

Read publication history with bounds
------------------------------------

.. literalinclude:: ../examples/publications.py
   :language: python
   :caption: launcher.py

``iter`` follows pages lazily. ``page_size`` controls records requested per page
and ``max_limit`` bounds total yielded records. ``scan_limit`` bounds publication
searches separately from returned matches. This example bounds both operations.
``Page.items`` contains the page's models; ``next_cursor`` is the next offset or
``None``. Concurrent changes can move offsets, so pagination is not a historical
snapshot or timestamp cursor.

Use several current resources
-----------------------------

.. literalinclude:: ../examples/standalone.py
   :language: python
   :caption: launcher.py

Live methods request data when invoked. Gifts resolve item rewards through the
shared catalog; unsupported reward types return ``None`` from that helper, while
missing required references raise a response error. ``None`` lottery or merchant
data means an explicit absence, not a failed request.

Watch changes without a bot
---------------------------

.. literalinclude:: ../examples/events.py
   :language: python
   :caption: launcher.py

This uses ``@dank.event`` and stays open until Ctrl+C. Only drops are polled;
discord.py is not needed. See :doc:`events` for schedules and :doc:`delivery`
to preserve detected callbacks across restarts.
