Live activity
=============

.. code-block:: python

   for gift in await dank.store_daily_gifts.today():
       print(gift.day.date(), gift.metadata.render)
       if gift.reward.item_id is not None:
           item = await dank.store_daily_gifts.reward_item(gift)
           if item is not None:
               print(item.name)

.. autoclass:: dankmemer.GlobalBoost
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Drop
   :members:
   :inherited-members:

.. autoclass:: dankmemer.StoreSale
   :members:
   :inherited-members:

.. autoclass:: dankmemer.StoreDailyGift
   :members:
   :inherited-members:

.. autoclass:: dankmemer.StoreGiftMetadata
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingEvent
   :members:
   :inherited-members:

.. autoclass:: dankmemer.LotteryResult
   :members:
   :inherited-members:

.. autoclass:: dankmemer.MerchantRotation
   :members:
   :inherited-members:

.. autoclass:: dankmemer.MerchantTrade
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Reward
   :members:
   :inherited-members:

