.. dankmemer.py documentation master file, created by
   sphinx-quickstart on Tue Apr  8 16:51:49 2025.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

dankmemer.py Documentation
==========================

Welcome to the documentation for dankmemer.py, a lightweight asynchronous
Python wrapper for Dank Memer item data. Version ``1.0.0rc3`` uses the limited
`Gwapes items API <https://api.gwapes.com/items>`_. With the default client,
only ``client.items`` is supported.

Important Service and Rules Notice
----------------------------------

.. warning::

   On 25 July 2026, Dank Memer announced Rule 13, `External Bots and
   Services <https://dankmemer.lol/rules>`_. The rule prohibits developing or
   using services that scrape Dank Memer's API, website, or Discord responses,
   and is due to be enforced from 1 September 2026.

   The third-party DankAlert API previously used by this package has shut down
   permanently, including its ``/items`` endpoint. Version ``1.0.0rc3`` no
   longer relies on DankAlert and supports only item data through Gwapes.

   If documentation for Dank Memer's `official API
   <https://dankmemer.lol/api>`_ becomes available, the intention is to migrate
   this package to use only that API and support its available features. A
   successfully migrated release would no longer rely on the prohibited
   third-party service. This migration is not guaranteed because the maintainer
   must first be approved for API access in order to test, develop, and complete
   it. No release date has been announced beyond the stated Q4 2026 target.

Installation
------------
Python 3.11 or newer is required. You can install dankmemer.py via pip:

.. code-block:: bash

   pip install dankmemer
   pip install dankmemer.py

Features
--------
- Built-in caching with configurable TTL
- Cache clearing and cache state introspection
- Configurable retries for rate limits, temporary server errors, timeouts, and connection errors
- Configurable logging modes for coloured package logs, silent operation, inherited logging, or a custom logger
- Powerful filtering (exact, fuzzy, membership [IN], numeric range, Above/Below/Range)
- Limited Gwapes item access through ``client.items``
- Anti-rate-limit protection

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   quickstart
   examples


.. toctree::
   :maxdepth: 2
   :caption: API Reference

   dankmemerclient
   routes
   objects
   exceptions


