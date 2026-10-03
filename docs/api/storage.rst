Event stores
============

The SDK owns its default memory store. You own any persistent store you supply
and must close it after the client finishes. See :doc:`../delivery` for complete
setup examples and delivery guarantees.

SQLite
------

.. autoclass:: dankmemer.storage.sqlite.SqliteEventStore
   :members:
   :exclude-members: __init__, __aenter__, __aexit__

PostgreSQL
----------

.. autoclass:: dankmemer.storage.postgres.PostgresEventStore
   :members:
   :exclude-members: __init__, __aenter__, __aexit__

Custom stores
-------------

Store methods and records are available without installing a database extra.
Structural protocol implementations need not inherit from these classes.
Implement every method on ``EventStore`` and its ``durable`` property.
Checkpoint writes and pending callback creation must share one atomic commit;
reading callbacks must leave them available until acknowledged. The methods
below describe version checks, ordering, capacity, and failure behaviour.

For shared coordination, implement ``CoordinatedEventStore`` as well. Its
operations use one shared transaction lock and the database's UTC clock to
prevent ownership from changing during event commits and acknowledgements.

.. autoclass:: dankmemer.EventStore
   :members:

.. autoclass:: dankmemer.CoordinatedEventStore
   :members:
   :inherited-members:

.. autoclass:: dankmemer.MemoryEventStore
   :members:

Stored records
--------------

.. autoclass:: dankmemer.CallbackIntent
   :members:

.. autoclass:: dankmemer.StoredCheckpoint
   :members:

.. autoclass:: dankmemer.StoredCallback
   :members:

.. autoclass:: dankmemer.CommitResult
   :members:

.. autoclass:: dankmemer.PendingCallbackLimit
