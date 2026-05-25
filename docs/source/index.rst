.. dankmemer.py documentation master file, created by
   sphinx-quickstart on Tue Apr  8 16:51:49 2025.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

dankmemer.py Documentation
==========================

Welcome to the documentation for dankmemer.py, a lightweight asynchronous Python wrapper for the DankAlert API.
Version ``1.0.0rc2`` supports the current DankAlert route groups exposed by the package.

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
- Stream metadata access through ``client.stream``
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


