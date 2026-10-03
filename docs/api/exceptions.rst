Exceptions
==========

Catch :class:`~dankmemer.DankMemerError` for library failures.
Use the more specific classes when your application can handle them differently.
HTTP and parsing failures are never converted into absent data.

.. autoclass:: dankmemer.DankMemerError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.ConfigurationError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.LifecycleError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.PaginationError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.AmbiguousLookupError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.DankMemerResponseError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.EventRecoveryError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.EventPayloadError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.DankMemerConnectionError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.DankMemerTimeoutError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.DankMemerHTTPError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.BadRequest
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.AuthenticationError
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.Forbidden
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.NotFound
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.RateLimited
   :members:
   :show-inheritance:

.. autoclass:: dankmemer.ServerError
   :members:
   :show-inheritance:


