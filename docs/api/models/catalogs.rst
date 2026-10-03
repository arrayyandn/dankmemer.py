Catalog records
===============

.. code-block:: python

   item = await dank.items.get("Life Saver")
   if item is not None:
       print(item.market_value)
       for skin in item.skins:
           print(skin.name, skin.reference)

.. autoclass:: dankmemer.Item
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Pet
   :members:
   :inherited-members:

.. autoclass:: dankmemer.PetStats
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Skin
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Command
   :members:
   :inherited-members:

.. autoclass:: dankmemer.CommandOption
   :members:
   :inherited-members:

