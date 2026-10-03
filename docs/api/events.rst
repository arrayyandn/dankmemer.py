Event reference
===============

These are async functions you define and register with the client.
They describe changes since the previous accepted read. Use the ``on_``
names with :meth:`~dankmemer.DankMemer.event`, or omit ``on_`` when supplying
a name to :meth:`~dankmemer.DankMemer.listen`.
Every callback returns ``None``.

See :doc:`../events` for startup behavior and polling schedules.

Active collections
------------------

.. py:function:: on_global_boosts_changed(before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]) -> None
   :async:

   The active boost collection changes.

.. py:function:: on_drops_changed(before: tuple[Drop, ...], after: tuple[Drop, ...]) -> None
   :async:

   The active drop collection changes.

.. py:function:: on_drop_started(drop: Drop) -> None
   :async:

   An ID appears in the active collection.

.. py:function:: on_drop_updated(before: Drop, after: Drop) -> None
   :async:

   Fields change for an existing ID.

.. py:function:: on_drop_ended(drop: Drop) -> None
   :async:

   An ID disappears; receives its last observed record.

.. py:function:: on_store_sales_changed(before: tuple[StoreSale, ...], after: tuple[StoreSale, ...]) -> None
   :async:

   The active sale collection changes.

.. py:function:: on_store_sale_started(sale: StoreSale) -> None
   :async:

   A sale ID appears.

.. py:function:: on_store_sale_updated(before: StoreSale, after: StoreSale) -> None
   :async:

   Fields change for an existing sale.

.. py:function:: on_store_sale_ended(sale: StoreSale) -> None
   :async:

   A sale ID disappears.

.. py:function:: on_fishing_events_changed(before: tuple[FishingEvent, ...], after: tuple[FishingEvent, ...]) -> None
   :async:

   The active fishing event collection changes.

.. py:function:: on_fishing_event_started(event: FishingEvent) -> None
   :async:

   A fishing event ID appears.

.. py:function:: on_fishing_event_updated(before: FishingEvent, after: FishingEvent) -> None
   :async:

   Fields change for an existing fishing event.

.. py:function:: on_fishing_event_ended(event: FishingEvent) -> None
   :async:

   A fishing event ID disappears.

Reordering alone does not produce events. Drops, sales, and fishing events
are matched by API ID. Boosts have no stable API ID, so they have only an
aggregate event. Started and ended describe observed collection membership;
they do not guarantee the exact moment the bot began or ended an activity.
No local timer invents an ended event between successful reads.

Daily gifts and streaming
-------------------------

.. py:function:: on_store_daily_gifts_changed(before: tuple[StoreDailyGift, ...], after: tuple[StoreDailyGift, ...]) -> None
   :async:

   The reported gift collection changes.

.. py:function:: on_store_daily_gift(gift: StoreDailyGift) -> None
   :async:

   A new UTC-day/pool-ID combination appears.

.. py:function:: on_store_daily_gift_updated(before: StoreDailyGift, after: StoreDailyGift) -> None
   :async:

   Fields change for the same day and pool ID.

.. py:function:: on_stream_trending_game(game: str) -> None
   :async:

   A game is observed for a newer UTC day.

.. py:function:: on_stream_trending_game_updated(before: str, after: str) -> None
   :async:

   The game changes within the same observed UTC day.

Gift pool IDs repeat across days, so their identity includes the UTC day.
Older nonempty gift-day snapshots are ignored.

The streaming endpoint supplies a game name without a date or revision.
Its day is the UTC day when the response is observed. The ordinary handler
can run on a new day even if the game name is unchanged. A delayed upstream
rotation cannot be distinguished from a repeated game; use interval polling
if you want later same-day changes to be observed. The default daily schedule
cannot confirm freshness beyond the returned value.

Results and publications
------------------------

.. py:function:: on_lottery_result(result: LotteryResult) -> None
   :async:

   A newer drawing time is observed.

.. py:function:: on_lottery_result_updated(before: LotteryResult, after: LotteryResult) -> None
   :async:

   Fields change for the same drawing time.

.. py:function:: on_merchant_rotation(rotation: MerchantRotation) -> None
   :async:

   A newer UTC-day rotation is observed.

.. py:function:: on_merchant_rotation_updated(before: MerchantRotation, after: MerchantRotation) -> None
   :async:

   The same day's rotation changes without an older generation time.

.. py:function:: on_blog_published(blog: Blog) -> None
   :async:

   A new publication ID is recovered ahead of the previous checkpoint.

.. py:function:: on_changelog_published(changelog: Changelog) -> None
   :async:

   A new changelog ID is recovered ahead of the previous checkpoint.

Older lottery and merchant data is ignored. An explicit null result after a
valid record does not erase its baseline.

Publication recovery reads one page per cycle and deduplicates IDs. It waits
until the previous checkpoint is found, then delivers the recovered entries
oldest first. Offset changes and deleted checkpoints can still cause a gap.
Such a gap raises :class:`~dankmemer.EventRecoveryError` and keeps the previous
baseline rather than admitting a partial batch.
