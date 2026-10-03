from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from dankmemer import (
    Blog,
    Changelog,
    Drop,
    FishingEvent,
    GlobalBoost,
    LotteryResult,
    MerchantRotation,
    StoreDailyGift,
    StoreSale,
)
from dankmemer.ext.dpy import dank_cog

if TYPE_CHECKING:
    from .bot import DankBot


class AlertsCog(commands.Cog):
    def __init__(self, bot: DankBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        dank_cog.bind(self, self.bot.dank)

    async def cog_unload(self) -> None:
        dank_cog.unbind(self)

    async def send_alert(self, text: str) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.bot.alert_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise RuntimeError(
                "ALERT_CHANNEL_ID must identify an accessible text channel or thread"
            )
        await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())

    @dank_cog.listen(subscription_id="alerts.drop_started")
    async def on_drop_started(self, drop: Drop) -> None:
        await self.send_alert(
            f"Drop {drop.id} ends <t:{int(drop.ends_at.timestamp())}:R>."
        )

    @dank_cog.listen(subscription_id="alerts.drop_updated")
    async def on_drop_updated(self, before: Drop, after: Drop) -> None:
        await self.send_alert(
            f"Drop {after.id} changed; its current per-user limit is {after.limit_per_user}."
        )

    @dank_cog.listen(subscription_id="alerts.drop_ended")
    async def on_drop_ended(self, drop: Drop) -> None:
        await self.send_alert(f"Drop {drop.id} is no longer reported active.")

    @dank_cog.listen(subscription_id="alerts.lottery")
    async def on_lottery_result(self, result: LotteryResult) -> None:
        await self.send_alert(
            f"Lottery drawn <t:{int(result.drawn_at.timestamp())}:f>: {result.winnings:,} winnings."
        )

    @dank_cog.listen(subscription_id="alerts.gifts")
    async def on_store_daily_gift(self, gift: StoreDailyGift) -> None:
        await self.send_alert(f"Today's {gift.category} gift: {gift.metadata.render}")

    @dank_cog.listen(subscription_id="alerts.merchant")
    async def on_merchant_rotation(self, rotation: MerchantRotation) -> None:
        await self.send_alert(
            f"Merchant offers for {rotation.date:%Y-%m-%d}: {len(rotation.trades)} trades."
        )

    @dank_cog.listen(subscription_id="alerts.sales")
    async def on_store_sale_started(self, sale: StoreSale) -> None:
        await self.send_alert(f"{sale.name} is on sale for {sale.price:g}: {sale.url}")

    @dank_cog.listen(subscription_id="alerts.fishing")
    async def on_fishing_event_started(self, event: FishingEvent) -> None:
        await self.send_alert(
            f"Fishing event: {event.name}. Ends <t:{int(event.ends_at.timestamp())}:R>."
        )

    @dank_cog.listen(subscription_id="alerts.streaming")
    async def on_stream_trending_game(self, game: str) -> None:
        await self.send_alert(f"The API's current streaming game: {game}")

    @dank_cog.listen(subscription_id="alerts.boosts")
    async def on_global_boosts_changed(
        self, before: tuple[GlobalBoost, ...], after: tuple[GlobalBoost, ...]
    ) -> None:
        boosts = ", ".join(f"{boost.type} ×{boost.multiplier:g}" for boost in after)
        await self.send_alert(f"Active global boosts: {boosts or 'none'}.")

    @dank_cog.listen(subscription_id="alerts.blogs")
    async def on_blog_published(self, blog: Blog) -> None:
        await self.send_alert(f"New blog: {blog.title}\n{blog.url}")

    @dank_cog.listen(subscription_id="alerts.changelogs")
    async def on_changelog_published(self, changelog: Changelog) -> None:
        await self.send_alert(f"New changelog: {changelog.title}\n{changelog.url}")


async def setup(bot: DankBot) -> None:
    cog = AlertsCog(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        dank_cog.unbind(cog)
        raise
