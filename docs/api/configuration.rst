Configuration
=============

All configuration objects are immutable. Omitted per-resource cache and polling
fields use the SDK defaults described in :doc:`../resources` and
:doc:`../events`.

Requests and application identity
---------------------------------

.. autoclass:: dankmemer.ApplicationIdentity
   :members:
   :undoc-members:

.. autoclass:: dankmemer.RequestConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.RetryConfig
   :members:
   :undoc-members:

Caching
-------

.. autoclass:: dankmemer.CacheConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.FishingCacheConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.TTLCache
   :members:
   :undoc-members:

.. autoclass:: dankmemer.NoCache
   :members:
   :undoc-members:

Polling
-------

.. autoclass:: dankmemer.PollingConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.IntervalPolling
   :members:
   :undoc-members:

.. autoclass:: dankmemer.HourlyPolling
   :members:
   :undoc-members:

.. autoclass:: dankmemer.DailyPolling
   :members:
   :undoc-members:

.. autoclass:: dankmemer.DisabledPolling
   :members:
   :undoc-members:

.. autoclass:: dankmemer.PollingResource
   :members:
   :undoc-members:

Delivery and coordination
-------------------------

.. autoclass:: dankmemer.EventConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.EventDelivery
   :members:
   :undoc-members:

.. autoclass:: dankmemer.CoordinationConfig
   :members:
   :undoc-members:

.. autoclass:: dankmemer.CoordinationMode
   :members:
   :undoc-members:

