Examples
========

These examples use fields supplied by the Gwapes items API.

Exact String Matching
---------------------

.. code-block:: python

    filtered = await client.items.query(ItemsFilter(name="Trash"))
    print([item.name for item in filtered])

Fuzzy Name Matching
-------------------

.. code-block:: python

    filtered = await client.items.query(
        ItemsFilter(name=Fuzzy("trash", cutoff=80))
    )
    print([item.name for item in filtered])

Membership Matching
-------------------

.. code-block:: python

    filtered = await client.items.query(ItemsFilter(name=IN("pepe", "apple")))
    print([item.name for item in filtered])

Market Value Range
------------------

.. code-block:: python

    filtered = await client.items.query(
        ItemsFilter(marketValue=(5000, 10000000))
    )
    print([item.name for item in filtered])

Net Value Helpers
-----------------

``netValue`` can be ``None`` when Gwapes does not provide it. Numeric filters
automatically exclude those entries.

.. code-block:: python

    above = await client.items.query(ItemsFilter(netValue=Above(10000)))
    below = await client.items.query(ItemsFilter(netValue=Below(10000)))
    ranged = await client.items.query(
        ItemsFilter(netValue=Range(10000, 5000000))
    )

Rarity and Type
---------------

Gwapes combines rarity and type, for example ``"Rare Loot Box"``. The adapter
exposes that value as ``rarity="Rare"`` and ``type="Loot Box"``.

.. code-block:: python

    rare_boxes = await client.items.query(
        ItemsFilter(rarity="Rare", type="Loot Box")
    )
    print([item.name for item in rare_boxes])
