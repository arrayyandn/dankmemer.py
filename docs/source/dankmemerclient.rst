DankMemerClient
===============

The DankMemerClient class manages asynchronous access to the Gwapes items API,
including caching and rate-limit protection. The default API supports only
``client.items``. Other routes raise
``dankmemer.exceptions.UnsupportedRouteException`` before an HTTP request.

An explicit non-Gwapes ``base_url`` preserves legacy request behaviour for a
compatible custom server, although such servers are not officially supported.

Python 3.11 or newer is required.

Cache Control
-------------

Route responses are cached for 24 hours by default. Use
``cache_ttl_hours=0`` or ``cache_ttl_hours=None`` to disable caching.

.. code-block:: python

    async with DankMemerClient(cache_ttl_hours=0) as client:
        items = await client.items.query()

Each route supports ``clear_cache()`` and ``cache_info()``. The client also
supports ``clear_cache()``, ``clear_route_cache("items")``, and ``cache_info()``
for all routes.

Retries and Logging
-------------------

``DankMemerClient`` retries HTTP 429 responses and selected temporary server
errors by default. ``Retry-After`` headers are respected when present.

.. code-block:: python

    client = DankMemerClient(
        retry_attempts=2,
        retry_backoff=0.5,
        retry_on_rate_limit=True,
    )

Logging is configured when a client is created, not at import time. The default
``logging_mode="default"`` installs the package's coloured console handler on
the ``dankmemer`` logger and avoids duplicate package-created handlers.

Use ``logging_mode="null"`` for silent logging, or
``logging_mode="inherit"`` to let parent/root application logging handle output.
Pass ``logger=...`` to use an application logger directly. Custom loggers are
not modified by the client.

.. code-block:: python

    default_client = DankMemerClient()
    explicit_default = DankMemerClient(logging_mode="default")
    silent_client = DankMemerClient(logging_mode="null")
    inherited_client = DankMemerClient(logging_mode="inherit")
    custom_client = DankMemerClient(logger=my_logger)

Session Ownership
-----------------

If the client creates its own ``aiohttp.ClientSession``, ``close()`` and the
async context manager close it. If you pass an existing session, ownership stays
with the caller and the client will not close it.

.. automodule:: dankmemer.client
    :members:
