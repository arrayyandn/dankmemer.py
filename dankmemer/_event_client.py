from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, TypeVar

from ._clock import Clock
from ._coordination import Coordinator, Lease, LeaseLost, independent_store
from ._coordination_storage import CoordinatedEventStore
from ._event_codecs import (
    blogs_codec,
    boosts_codec,
    changelogs_codec,
    drops_codec,
    fishing_events_codec,
    gifts_codec,
    lottery_codec,
    merchant_codec,
    sales_codec,
    trending_codec,
)
from ._event_sources import (
    Observer,
    PublicationSource,
    PublicationState,
    SnapshotSource,
    Source,
    boost_changes,
    lottery_allowed,
    lottery_changes,
    merchant_allowed,
    merchant_changes,
    publication_allowed,
    publication_changes,
)
from ._events import _DemandScheduler, is_async_callback, same_callback
from ._live_events import (
    TrendingGameState,
    drop_changes,
    fishing_event_changes,
    gift_changes,
    gifts_allowed,
    sale_changes,
    trending_allowed,
    trending_changes,
)
from ._observations import Emission
from ._parsing import Record
from ._storage import EventStore, MemoryEventStore, checked_subscription_id
from ._stored_events import EventCodec, StoredEventPipeline
from .config import (
    DailyPolling,
    DisabledPolling,
    EventConfig,
    HourlyPolling,
    IntervalPolling,
    PollingConfig,
)
from .enums import EventDelivery, PollingResource
from .errors import ConfigurationError, EventRecoveryError, LifecycleError
from .http._routes import (
    BLOGS,
    CHANGELOGS,
    DROPS,
    FISHING_EVENTS,
    GLOBAL_BOOSTS,
    LOTTERY,
    MERCHANT_TRADES,
    STORE_DAILY_GIFTS,
    STORE_SALES,
    STREAM_TRENDING_GAME,
    Route,
)
from .models.activities import (
    GlobalBoost,
    LotteryResult,
    MerchantRotation,
    parse_global_boost,
    parse_lottery,
    parse_merchant_rotation,
)
from .models.live import (
    Drop,
    FishingEvent,
    StoreDailyGift,
    StoreSale,
    parse_drop,
    parse_fishing_event,
    parse_live_collection,
    parse_store_daily_gift,
    parse_store_sale,
)
from .models.publications import Blog, Changelog, parse_blog, parse_changelog
from .resources._base import PaginatedResource, Requester

DEPENDENCIES = {
    "global_boosts_changed": PollingResource.GLOBAL_BOOSTS,
    "lottery_result": PollingResource.LOTTERY,
    "lottery_result_updated": PollingResource.LOTTERY,
    "merchant_rotation": PollingResource.MERCHANT_TRADES,
    "merchant_rotation_updated": PollingResource.MERCHANT_TRADES,
    "blog_published": PollingResource.BLOGS,
    "changelog_published": PollingResource.CHANGELOGS,
    "drops_changed": PollingResource.DROPS,
    "drop_started": PollingResource.DROPS,
    "drop_updated": PollingResource.DROPS,
    "drop_ended": PollingResource.DROPS,
    "store_sales_changed": PollingResource.STORE_SALES,
    "store_sale_started": PollingResource.STORE_SALES,
    "store_sale_updated": PollingResource.STORE_SALES,
    "store_sale_ended": PollingResource.STORE_SALES,
    "fishing_events_changed": PollingResource.FISHING_EVENTS,
    "fishing_event_started": PollingResource.FISHING_EVENTS,
    "fishing_event_updated": PollingResource.FISHING_EVENTS,
    "fishing_event_ended": PollingResource.FISHING_EVENTS,
    "store_daily_gifts_changed": PollingResource.STORE_DAILY_GIFTS,
    "store_daily_gift": PollingResource.STORE_DAILY_GIFTS,
    "store_daily_gift_updated": PollingResource.STORE_DAILY_GIFTS,
    "stream_trending_game": PollingResource.STREAM_TRENDING_GAME,
    "stream_trending_game_updated": PollingResource.STREAM_TRENDING_GAME,
}

Policy = IntervalPolling | HourlyPolling | DailyPolling
_T = TypeVar("_T")
_Listener = Callable[..., Awaitable[None]]
_ARGUMENT_COUNTS = {
    event: (2 if event.endswith(("_updated", "_changed")) else 1)
    for event in DEPENDENCIES
}


class _Pipeline(Protocol):
    @property
    def delivery_tasks(self) -> frozenset[asyncio.Task[None]]: ...

    def stop(self) -> None: ...

    @property
    def paused_subscriptions(self) -> frozenset[str]: ...
    @property
    def delivery_errors(self) -> Mapping[str, Exception]: ...
    @property
    def in_flight_ids(self) -> frozenset[int]: ...
    def in_flight_id(self, subscription_id: str) -> int | None: ...
    def add_listener(
        self, event: str, subscription_id: str, callback: _Listener
    ) -> None: ...
    def detach_listener(self, subscription_id: str, *, cancel: bool = True) -> bool: ...
    def retry_pending(self, subscription_id: str) -> None: ...
    async def prepare(self) -> None: ...
    async def refresh(self, lease: Lease) -> None: ...
    def sync_shared(self) -> None: ...
    def activate(self) -> None: ...
    async def close(self, *, caller: asyncio.Task[object] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class _Registration:
    event: str
    subscription_id: str
    callback: _Listener


def _blog_changes(
    previous: PublicationState[Blog] | None,
    current: PublicationState[Blog],
    initial: bool,
) -> tuple[Emission, ...]:
    return publication_changes("blog_published", previous, current)


def _changelog_changes(
    previous: PublicationState[Changelog] | None,
    current: PublicationState[Changelog],
    initial: bool,
) -> tuple[Emission, ...]:
    return publication_changes("changelog_published", previous, current)


def polling_policies(config: PollingConfig) -> dict[PollingResource, Policy]:
    defaults: dict[PollingResource, Policy | DisabledPolling] = {
        PollingResource.GLOBAL_BOOSTS: config.global_boosts
        or IntervalPolling(seconds=60),
        PollingResource.BLOGS: config.blogs or IntervalPolling(hours=1),
        PollingResource.CHANGELOGS: config.changelogs or IntervalPolling(hours=1),
        PollingResource.LOTTERY: config.lottery or HourlyPolling(),
        PollingResource.MERCHANT_TRADES: config.merchant_trades or DailyPolling(),
        PollingResource.DROPS: config.drops or IntervalPolling(seconds=60),
        PollingResource.STORE_SALES: config.store_sales or IntervalPolling(seconds=60),
        PollingResource.FISHING_EVENTS: config.fishing_events
        or IntervalPolling(seconds=60),
        PollingResource.STORE_DAILY_GIFTS: config.store_daily_gifts or DailyPolling(),
        PollingResource.STREAM_TRENDING_GAME: config.stream_trending_game
        or DailyPolling(),
    }
    return {
        resource: policy
        for resource, policy in defaults.items()
        if not isinstance(policy, DisabledPolling)
    }


def calendar_delay(
    policy: HourlyPolling | DailyPolling, wall_time: float, observed_at: float | None
) -> float:
    period = 3600 if isinstance(policy, HourlyPolling) else 86400
    boundary = math.floor(wall_time / period) * period
    due = boundary + policy.offset_seconds
    next_due = due + period
    if observed_at is not None and observed_at >= boundary:
        target = next_due
    elif wall_time < due:
        target = due
    elif wall_time < due + policy.retry_window_minutes * 60:
        target = min(next_due, wall_time + policy.retry_seconds)
    else:
        target = min(next_due, wall_time + policy.reconcile_minutes * 60)
    return max(60.0, target - wall_time)


class EventClient:
    """Keep polling demand separate from each resource's delivery pipeline."""

    def __init__(
        self,
        request: Requester,
        policies: Mapping[PollingResource, Policy],
        config: EventConfig,
        *,
        clock: Clock,
        logger: logging.Logger,
        startup_jitter: float,
        store: EventStore | None = None,
        coordinator: Coordinator | None = None,
    ) -> None:
        self._request = request
        self._policies = dict(policies)
        self._clock = clock
        self._coordinator = coordinator
        self._poll_leases: dict[PollingResource, Lease] = {}
        self._config = config
        self._logger = logger
        self._memory = (
            MemoryEventStore(max_pending_callbacks=config.max_pending_deliveries)
            if config.delivery is EventDelivery.BEST_EFFORT
            else None
        )
        selected_store = self._memory if self._memory is not None else store
        if selected_store is None:
            raise ConfigurationError("durable delivery requires an event_store")
        self._store: EventStore = selected_store
        self._pipelines: dict[PollingResource, _Pipeline] = {}
        self._registrations: dict[int, _Registration] = {}
        self._bindings: dict[str, str] = {}
        self._starts: dict[PollingResource, asyncio.Task[None]] = {}
        self._running = False
        self._closed = False
        self._errors: dict[PollingResource, Exception] = {}
        self.scheduler = _DemandScheduler(
            DEPENDENCIES,
            {
                resource: 60.0
                if coordinator is None
                else coordinator.config.check_seconds
                for resource in policies
            },
            self._poll,
            clock=clock,
            logger=logger,
            next_delay=self._next_delay,
            initial_delay=lambda: random.uniform(0, startup_jitter),
            minimum_interval=60.0
            if coordinator is None
            else coordinator.config.check_seconds,
            expected_cancel=self._expected_cancel if coordinator is not None else None,
        )
        if coordinator is not None:
            coordinator.on_change = self._sync_shared
        blogs = PaginatedResource(
            self._automatic_request, BLOGS, parse_blog, clock=clock
        )
        changelogs = PaginatedResource(
            self._automatic_request, CHANGELOGS, parse_changelog, clock=clock
        )
        self._readers = (blogs, changelogs)
        self._sources: dict[PollingResource, Source] = {
            PollingResource.DROPS: SnapshotSource(
                self._drops,
                self._observer(PollingResource.DROPS, drop_changes, drops_codec()),
            ),
            PollingResource.STORE_SALES: SnapshotSource(
                self._sales,
                self._observer(
                    PollingResource.STORE_SALES, sale_changes, sales_codec()
                ),
            ),
            PollingResource.FISHING_EVENTS: SnapshotSource(
                self._fishing_events,
                self._observer(
                    PollingResource.FISHING_EVENTS,
                    fishing_event_changes,
                    fishing_events_codec(),
                ),
            ),
            PollingResource.STORE_DAILY_GIFTS: SnapshotSource(
                self._gifts,
                self._observer(
                    PollingResource.STORE_DAILY_GIFTS,
                    gift_changes,
                    gifts_codec(),
                    allowed=gifts_allowed,
                ),
                allowed=gifts_allowed,
                timestamp=lambda values: max(
                    (gift.day.timestamp() for gift in values), default=None
                ),
            ),
            PollingResource.STREAM_TRENDING_GAME: SnapshotSource(
                self._trending,
                self._observer(
                    PollingResource.STREAM_TRENDING_GAME,
                    trending_changes,
                    trending_codec(),
                    allowed=trending_allowed,
                ),
                allowed=trending_allowed,
                timestamp=lambda value: value.day.timestamp(),
            ),
            PollingResource.GLOBAL_BOOSTS: SnapshotSource(
                self._boosts,
                self._observer(
                    PollingResource.GLOBAL_BOOSTS, boost_changes, boosts_codec()
                ),
            ),
            PollingResource.LOTTERY: SnapshotSource[LotteryResult | None](
                self._lottery,
                self._observer(
                    PollingResource.LOTTERY,
                    lottery_changes,
                    lottery_codec(),
                    allowed=lottery_allowed,
                ),
                allowed=lottery_allowed,
                timestamp=lambda value: (
                    None if value is None else value.drawn_at.timestamp()
                ),
            ),
            PollingResource.MERCHANT_TRADES: SnapshotSource[MerchantRotation | None](
                self._merchant,
                self._observer(
                    PollingResource.MERCHANT_TRADES,
                    merchant_changes,
                    merchant_codec(),
                    allowed=merchant_allowed,
                ),
                allowed=merchant_allowed,
                timestamp=lambda value: (
                    None if value is None else value.date.timestamp()
                ),
            ),
            PollingResource.BLOGS: PublicationSource(
                blogs,
                config,
                self._observer(
                    PollingResource.BLOGS,
                    _blog_changes,
                    blogs_codec(),
                    allowed=publication_allowed,
                ),
            ),
            PollingResource.CHANGELOGS: PublicationSource(
                changelogs,
                config,
                self._observer(
                    PollingResource.CHANGELOGS,
                    _changelog_changes,
                    changelogs_codec(),
                    allowed=publication_allowed,
                ),
            ),
        }

    def _observer(
        self,
        resource: PollingResource,
        derive: Callable[[_T | None, _T, bool], Sequence[Emission]],
        codec: EventCodec[_T],
        *,
        allowed: Callable[[_T, _T], bool] | None = None,
    ) -> Observer[_T]:
        pipeline = StoredEventPipeline(
            resource,
            derive,
            codec,
            self._store,
            emit_initial=self._config.emit_initial,
            logger=self._logger,
            best_effort=self._memory is not None,
            max_pending_callbacks=self._config.max_pending_deliveries,
            allowed=allowed,
            argument_counts=_ARGUMENT_COUNTS,
            coordinator=self._coordinator,
            clock=self._clock,
        )
        self._pipelines[resource] = pipeline
        return pipeline

    def add_listener(
        self,
        event: str,
        callback: _Listener,
        *,
        subscription_id: str | None = None,
        replace_id: int | None = None,
    ) -> int:
        existing = self.validate_listener(
            event, callback, subscription_id=subscription_id, replace_id=replace_id
        )
        if existing is not None:
            return existing

        listener_id = self.scheduler.add_listener(event, callback)
        if replace_id is not None:
            self.remove_listener(replace_id)
        selected_id = (
            subscription_id if subscription_id is not None else f"memory:{listener_id}"
        )
        pipeline = self._pipelines[DEPENDENCIES[event]]
        pipeline.add_listener(event, selected_id, callback)
        self._registrations[listener_id] = _Registration(event, selected_id, callback)
        self._bindings[selected_id] = event
        if self._coordinator is not None:
            self._coordinator.register(selected_id, event)
        if self._running:
            self._start_task(DEPENDENCIES[event])
        return listener_id

    def validate_listener(
        self,
        event: str,
        callback: _Listener,
        *,
        subscription_id: str | None = None,
        replace_id: int | None = None,
    ) -> int | None:
        """Check registration without changing listeners or polling demand."""
        if self._closed:
            raise LifecycleError("event client is closed")
        if event not in DEPENDENCIES:
            raise ConfigurationError("unknown event name")
        if not is_async_callback(callback):
            raise ConfigurationError("event listener must be an async callable")
        if subscription_id is not None:
            checked_subscription_id(subscription_id)
            if (
                subscription_id in self._bindings
                and self._bindings[subscription_id] != event
            ):
                raise ConfigurationError(
                    "subscription_id cannot be reused for another event"
                )
        elif self._memory is None:
            raise ConfigurationError(
                "durable listeners require a stable subscription_id"
            )
        for listener_id, registration in self._registrations.items():
            if registration.event == event and same_callback(
                registration.callback, callback
            ):
                if (
                    subscription_id is not None
                    and subscription_id != registration.subscription_id
                ):
                    raise ConfigurationError(
                        "callback is already registered with another subscription_id"
                    )
                return listener_id
            if (
                registration.subscription_id == subscription_id
                and listener_id != replace_id
            ):
                raise ConfigurationError("subscription_id is already registered")

        return None

    def find_listener(self, event: str, callback: _Listener) -> int | None:
        if not is_async_callback(callback):
            raise ConfigurationError("listener must be an async callback")
        for listener_id, registration in self._registrations.items():
            if registration.event == event and same_callback(
                registration.callback, callback
            ):
                return listener_id
        return None

    def remove_listener(self, listener_id: int) -> bool:
        registration = self._registrations.pop(listener_id, None)
        if registration is None:
            return False
        pipeline = self._pipelines[DEPENDENCIES[registration.event]]
        pipeline.detach_listener(
            registration.subscription_id, cancel=self._memory is None
        )
        if self._memory is not None:
            self._memory._discard_pending(  # pyright: ignore[reportPrivateUsage]
                registration.subscription_id,
                keep_id=pipeline.in_flight_id(registration.subscription_id),
            )  # pyright: ignore[reportPrivateUsage]
        self.scheduler.remove_listener(listener_id)
        if self._coordinator is not None:
            self._coordinator.unregister(registration.subscription_id)
        return True

    async def count_pending(self, subscription_id: str | None) -> int:
        return await self._store.count_pending_callbacks(subscription_id)

    @property
    def paused_subscriptions(self) -> frozenset[str]:
        return frozenset(
            subscription_id
            for pipeline in self._pipelines.values()
            for subscription_id in pipeline.paused_subscriptions
        )

    @property
    def delivery_errors(self) -> Mapping[str, Exception]:
        return MappingProxyType(
            {
                subscription_id: error
                for pipeline in self._pipelines.values()
                for subscription_id, error in pipeline.delivery_errors.items()
            }
        )

    def retry_pending(self, subscription_id: str) -> None:
        checked_subscription_id(subscription_id)
        if self._memory is not None:
            raise ConfigurationError("retry_pending requires durable delivery")
        if not self._running:
            raise LifecycleError("client must be started before retrying pending calls")
        for registration in self._registrations.values():
            if registration.subscription_id == subscription_id:
                if self._coordinator is not None:
                    self._coordinator.resume(subscription_id)
                    return
                self._pipelines[DEPENDENCIES[registration.event]].retry_pending(
                    subscription_id
                )
                return
        raise ConfigurationError("unknown subscription_id")

    def _needed_resources(self) -> frozenset[PollingResource]:
        return self.scheduler.active_resources | frozenset(
            DEPENDENCIES[registration.event]
            for registration in self._registrations.values()
        )

    async def prepare(self) -> None:
        if self._coordinator is not None:
            await self._coordinator.prepare()
        elif isinstance(self._store, CoordinatedEventStore):
            await self._store.coordinate(independent_store)
        # No callbacks start until every needed baseline has loaded successfully.
        prepared: set[PollingResource] = set()
        while resources := self._needed_resources() - prepared:
            for resource in sorted(resources, key=lambda resource: resource.value):
                await self._pipelines[resource].prepare()
                prepared.add(resource)
            # A listener can be registered while a store read is awaiting.

    async def sync_subscriptions(self) -> None:
        if self._coordinator is None:
            raise ConfigurationError("subscription sync requires shared coordination")
        await self._coordinator.flush()
        await self._coordinator.prepare()

    async def start(self) -> None:
        """Activate prepared pipelines and start their polling workers."""
        if self._closed:
            raise LifecycleError("event client is closed")
        self._running = True
        for resource in self._needed_resources():
            self._pipelines[resource].activate()
        await self.scheduler.start()
        if self._coordinator is not None:
            self._coordinator.start()

    def _sync_shared(self) -> None:
        if self._closed or self._coordinator is None:
            return
        self.scheduler.set_external_resources(self._coordinator.resources)
        for pipeline in self._pipelines.values():
            pipeline.sync_shared()
        if self._running:
            for resource in self._needed_resources():
                self._start_task(resource)

    def _expected_cancel(self, resource: PollingResource) -> bool:
        return self._coordinator is not None and not self._coordinator.owns(
            "resource:" + resource.value
        )

    def _start_task(self, resource: PollingResource) -> asyncio.Task[None]:
        task = self._starts.get(resource)
        if task is None or (
            task.done() and (task.cancelled() or task.exception() is not None)
        ):
            task = asyncio.create_task(self._start_resource(resource))
            self._starts[resource] = task
            task.add_done_callback(
                lambda finished: self._start_done(resource, finished)
            )
        return task

    async def _start_resource(self, resource: PollingResource) -> None:
        await self._pipelines[resource].prepare()
        if self._running:
            self._pipelines[resource].activate()

    def _start_done(self, resource: PollingResource, task: asyncio.Task[None]) -> None:
        if task.cancelled() or not self._running:
            return
        if (error := task.exception()) is not None:
            self._errors[resource] = (
                error
                if isinstance(error, Exception)
                else LifecycleError("event startup was interrupted")
            )
            if resource not in self._policies:
                self._logger.error(
                    "could not prepare event resource %s (%s)",
                    resource,
                    type(error).__name__,
                )

    @property
    def errors(self) -> Mapping[PollingResource, Exception]:
        return MappingProxyType(dict(self._errors))

    async def _automatic_request(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        return await self._request(
            route,
            automatic=True,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
            category=category,
        )

    async def _record(self, route: Route) -> Record:
        return Record(
            await self._request(route, automatic=True), path=route.template, field=""
        )

    async def _boosts(self) -> tuple[GlobalBoost, ...]:
        response = await self._record(GLOBAL_BOOSTS)
        # IDs are unavailable; collection order alone must not report a change.
        return tuple(
            sorted(
                (parse_global_boost(record) for record in response.records("data")),
                key=lambda boost: (boost.type, boost.ends_at, boost.multiplier),
            )
        )

    async def _lottery(self) -> LotteryResult | None:
        record = (await self._record(LOTTERY)).nullable_record("data")
        return None if record is None else parse_lottery(record)

    async def _drops(self) -> tuple[Drop, ...]:
        return tuple(
            sorted(
                parse_live_collection(await self._record(DROPS), parse_drop),
                key=lambda value: value.id,
            )
        )

    async def _sales(self) -> tuple[StoreSale, ...]:
        return tuple(
            sorted(
                parse_live_collection(
                    await self._record(STORE_SALES), parse_store_sale
                ),
                key=lambda value: value.id,
            )
        )

    async def _fishing_events(self) -> tuple[FishingEvent, ...]:
        return tuple(
            sorted(
                parse_live_collection(
                    await self._record(FISHING_EVENTS), parse_fishing_event
                ),
                key=lambda value: value.id,
            )
        )

    async def _gifts(self) -> tuple[StoreDailyGift, ...]:
        return tuple(
            sorted(
                parse_live_collection(
                    await self._record(STORE_DAILY_GIFTS), parse_store_daily_gift
                ),
                key=lambda value: value.id,
            )
        )

    async def _trending(self) -> TrendingGameState:
        response = await self._record(STREAM_TRENDING_GAME)
        # The route has no day field; retain the UTC day on which it was observed.
        day = datetime.fromtimestamp(self._clock.time(), UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return TrendingGameState(day, response.record("data").string("game"))

    async def _merchant(self) -> MerchantRotation | None:
        record = (await self._record(MERCHANT_TRADES)).nullable_record("data")
        return None if record is None else parse_merchant_rotation(record)

    async def _poll(self, resource: PollingResource, events: frozenset[str]) -> None:
        lease: Lease | None = None
        due = False
        try:
            await self._start_task(resource)
            if self._coordinator is not None:
                lease, due = await self._coordinator.claim_poll(resource)
                if lease is None or not due:
                    return
                if self._poll_leases.get(resource) != lease:
                    self._sources[resource].reset()
                    await self._pipelines[resource].refresh(lease)
                    self._poll_leases[resource] = lease
            elif isinstance(self._store, CoordinatedEventStore):
                await self._store.coordinate(independent_store)
            if self._coordinator is None:
                await self._sources[resource].poll()
            else:
                assert lease is not None
                with self._coordinator.polling(lease):
                    await self._sources[resource].poll()
        except LeaseLost:
            self._sources[resource].reset()
            if self._coordinator is not None and lease is not None and due:
                self._coordinator.forget(lease)
            self._poll_leases.pop(resource, None)
        except Exception as error:
            self._errors[resource] = error
            raise
        else:
            self._errors.pop(resource, None)
        finally:
            if self._coordinator is not None and lease is not None and due:
                try:
                    await self._coordinator.finish_poll(
                        resource, lease, lambda epoch: self._poll_delay(resource, epoch)
                    )
                except LeaseLost:
                    self._coordinator.forget(lease)
                    self._poll_leases.pop(resource, None)

    def _next_delay(self, resource: PollingResource) -> float:
        if self._coordinator is not None:
            return self._coordinator.config.check_seconds
        return self._poll_delay(resource, self._clock.time())

    def _poll_delay(self, resource: PollingResource, epoch: float) -> float:
        source = self._sources[resource]
        policy = self._policies[resource]
        if source.recovering and not isinstance(
            self._errors.get(resource), EventRecoveryError
        ):
            return 60.0
        if isinstance(policy, IntervalPolling):
            return policy.interval.total_seconds()
        return calendar_delay(policy, epoch, source.observed_at)

    @property
    def delivery_tasks(self) -> frozenset[asyncio.Task[None]]:
        return frozenset(
            task
            for pipeline in self._pipelines.values()
            for task in pipeline.delivery_tasks
        )

    async def stop(self, *, caller: asyncio.Task[object] | None = None) -> None:
        """Stop producing work without closing running deliveries or their store."""
        self._closed = True
        self._running = False
        for pipeline in self._pipelines.values():
            pipeline.stop()
        try:
            await self.scheduler.close(caller=caller)
        finally:
            for task in self._starts.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._starts.values(), return_exceptions=True)

    async def close(self, *, caller: asyncio.Task[object] | None = None) -> None:
        try:
            await self.stop(caller=caller)
        finally:
            try:
                try:
                    await asyncio.gather(
                        *(
                            pipeline.close(caller=caller)
                            for pipeline in self._pipelines.values()
                        )
                    )
                finally:
                    await asyncio.gather(*(reader._close() for reader in self._readers))  # pyright: ignore[reportPrivateUsage]
            finally:
                if self._coordinator is not None:
                    await self._coordinator.close(caller=caller)
                if self._memory is not None:
                    self._memory._clear(  # pyright: ignore[reportPrivateUsage]
                        keep_ids=frozenset(
                            callback_id
                            for pipeline in self._pipelines.values()
                            for callback_id in pipeline.in_flight_ids
                        )
                    )  # pyright: ignore[reportPrivateUsage]
                self._sources.clear()
                self._pipelines.clear()
                self._registrations.clear()
                self._bindings.clear()
                self._starts.clear()
                self._errors.clear()
