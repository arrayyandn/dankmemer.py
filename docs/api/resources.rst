Resource methods
================

Use the resource attributes on :class:`~dankmemer.DankMemer`; you do not need
to construct resource classes yourself. Each method below shows the models
and ID types returned or accepted by that resource.

.. list-table:: Client attributes
   :header-rows: 1

   * - Attribute
     - Model / result
   * - ``dank.items``
     - Item
   * - ``dank.pets``
     - Pet
   * - ``dank.skins``
     - Skin
   * - ``dank.commands``
     - Command
   * - ``dank.blogs``
     - Blog
   * - ``dank.changelogs``
     - Changelog
   * - ``dank.fishing.creatures``
     - FishingCreature
   * - ``dank.fishing.locations``
     - FishingLocation
   * - ``dank.fishing.npcs``
     - FishingNPC
   * - ``dank.fishing.tools``
     - FishingTool
   * - ``dank.fishing.baits``
     - FishingBait
   * - ``dank.fishing.buckets``
     - FishingBucket
   * - ``dank.fishing.skills``
     - FishingSkill
   * - ``dank.drops``
     - Drop
   * - ``dank.store_sales``
     - StoreSale
   * - ``dank.store_daily_gifts``
     - StoreDailyGift
   * - ``dank.fishing_events``
     - FishingEvent
   * - ``dank.global_boosts``
     - GlobalBoost
   * - ``dank.lottery``
     - LotteryResult or None
   * - ``dank.merchant_trades``
     - MerchantRotation or None
   * - ``dank.stream``
     - str
   * - ``dank.users``
     - bool

.. toctree::
   :maxdepth: 1

   resources/catalogs
   resources/fishing
   resources/activities
   resources/publications

Pagination result
-----------------

``fetch`` returns a page of the resource's models. For example, an item page
is a ``Page[Item]``: its ``items`` are a tuple of items and its
``next_cursor`` is the offset for the next page, or ``None`` when there are
no more pages.

.. autoclass:: dankmemer.Page()
   :members:

