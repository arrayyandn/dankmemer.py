from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from datetime import timedelta
from types import TracebackType
from typing import Self, TypeVar, cast, overload
from urllib.parse import urlsplit

import aiohttp

from dankmemer.enums import (
    ClientState,
    CoordinationMode,
    EventDelivery,
    PollingResource,
)

from ._coordination import Coordinator
from ._coordination_storage import CoordinatedEventStore
from ._event_client import DEPENDENCIES, EventClient, polling_policies
from ._events import same_callback
from ._storage import EventStore
from .config import (
    ApplicationIdentity,
    CacheConfig,
    CachePolicy,
    CoordinationConfig,
    EventConfig,
    FishingCacheConfig,
    NoCache,
    PollingConfig,
    RequestConfig,
    RetryConfig,
    TTLCache,
    _number,  # pyright: ignore[reportPrivateUsage]
)
from .errors import ConfigurationError, LifecycleError
from .http._admission import Admission
from .http._routes import Route
from .http._transport import Transport
from .resources import (
    Blogs,
    Changelogs,
    Commands,
    Drops,
    Fishing,
    FishingEvents,
    GlobalBoosts,
    Items,
    Lottery,
    MerchantTrades,
    Pets,
    Skins,
    StoreDailyGifts,
    StoreSales,
    Stream,
    Users,
)
from .resources._base import Resource

_T = TypeVar("_T")
_Listener = Callable[..., Awaitable[None]]
_Callback = TypeVar("_Callback", bound=_Listener)


def _cache_policy(policy: CachePolicy | None, default: CachePolicy) -> CachePolicy:
    return policy if policy is not None else default


def _valid_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise ConfigurationError("token must be a nonempty, header-safe string")
    return value


def _optional_instance(
    value: object | None, expected: type[_T], name: str
) -> _T | None:
    if value is not None and not isinstance(value, expected):
        raise ConfigurationError(f"{name} must be {expected.__name__}")
    return value


def _valid_event_store(value: object | None) -> EventStore | None:
    if value is None:
        return None
    if not isinstance(value, EventStore) or type(value.durable) is not bool:
        raise ConfigurationError("event_store must implement EventStore")
    return value


def _valid_origin(origin: object) -> str:
    if (
        not isinstance(origin, str)
        or not origin
        or any(ord(char) < 33 for char in origin)
    ):
        raise ConfigurationError(
            "origin must be an HTTPS scheme, host, and optional port"
        )
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        raise ConfigurationError(
            "origin must be an HTTPS scheme, host, and optional port"
        ) from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and port == 0)
    ):
        raise ConfigurationError(
            "origin must be an HTTPS scheme, host, and optional port"
        )
    return origin


class DankMemer:
    """An asynchronous client for the Dank Memer official API.

    The HTTP session is prepared by :meth:`start` or the first request.
    The client then stays bound to that event loop. A supplied session remains
    the caller's responsibility; the client closes only sessions it creates.
    Resource methods make requests on demand. After :meth:`start`, event
    polling runs only for resources needed by registered listeners.

    Parameters
    ----------
    token
        Developer token sent as a Bearer token.
    origin
        Optional HTTPS origin sent in the ``Origin`` header.
    application
        Application name and version to include in the ``User-Agent`` header.
    session
        An existing ``aiohttp`` session owned by the caller.
    request
        Network timeout and response size limits.
    retry
        Retry and backoff settings.
    cache
        Per-resource cache settings. Reference catalogs default to one hour.
        Publications and live resources default to no caching. These are SDK
        defaults. Expiry is checked on access and does not start background
        requests.
    polling
        Per-resource event polling schedules. Automatic request attempts to
        the same resource remain at least 60 seconds apart.
    events
        Delivery mode, initial event emission, and callback queue limits.
    event_store
        Store implementing ``EventStore`` for durable callback replay. Required
        with ``EventDelivery.DURABLE`` and rejected for best-effort delivery.
        The caller owns this store and must keep it open while the client runs.
    coordination
        Independent polling by default. Shared mode uses a persistent
        ``CoordinatedEventStore`` and the application's public ID to share
        polling, callback ownership, and request allowances across processes.
    logger
        Custom logger for client errors. Omit to use the ``dankmemer`` logger.
    silent
        Suppress client logging. Cannot be combined with ``logger``.
    """

    def __init__(
        self,
        token: str,
        *,
        origin: str | None = None,
        application: ApplicationIdentity | None = None,
        session: aiohttp.ClientSession | None = None,
        request: RequestConfig | None = None,
        retry: RetryConfig | None = None,
        cache: CacheConfig | None = None,
        polling: PollingConfig | None = None,
        events: EventConfig | None = None,
        event_store: EventStore | None = None,
        coordination: CoordinationConfig | None = None,
        logger: logging.Logger | None = None,
        silent: bool = False,
    ) -> None:
        token = _valid_token(token)
        if origin is not None:
            origin = _valid_origin(origin)
        application = _optional_instance(
            application, ApplicationIdentity, "application"
        )
        session = _optional_instance(session, aiohttp.ClientSession, "session")
        request = _optional_instance(request, RequestConfig, "request")
        retry = _optional_instance(retry, RetryConfig, "retry")
        cache = _optional_instance(cache, CacheConfig, "cache")
        polling = _optional_instance(polling, PollingConfig, "polling")
        polling = polling if polling is not None else PollingConfig()
        policies = polling_policies(polling)
        events = _optional_instance(events, EventConfig, "events")
        events = events if events is not None else EventConfig()
        event_store = _valid_event_store(event_store)
        coordination = _optional_instance(
            coordination, CoordinationConfig, "coordination"
        )
        coordination = (
            coordination if coordination is not None else CoordinationConfig()
        )
        if event_store is not None:
            if events.delivery is not EventDelivery.DURABLE:
                raise ConfigurationError("event_store requires EventDelivery.DURABLE")
        if events.delivery is EventDelivery.DURABLE:
            if event_store is None or not event_store.durable:
                raise ConfigurationError(
                    "durable delivery requires a persistent event_store"
                )
        if coordination.mode is CoordinationMode.SHARED:
            if events.delivery is not EventDelivery.DURABLE or not isinstance(
                event_store, CoordinatedEventStore
            ):
                raise ConfigurationError(
                    "shared coordination requires durable delivery and a CoordinatedEventStore"
                )
        cache = cache if cache is not None else CacheConfig()
        logger = _optional_instance(logger, logging.Logger, "logger")
        if type(silent) is not bool:
            raise ConfigurationError("silent must be bool")
        if silent and logger is not None:
            raise ConfigurationError("silent cannot be combined with logger")

        self._token = token
        self._origin = origin
        self._application = application
        self._request = request if request is not None else RequestConfig()
        self._retry = retry if retry is not None else RetryConfig()
        if silent:
            self._logger = logging.Logger("dankmemer.silent")
            self._logger.addHandler(logging.NullHandler())
            self._logger.propagate = False
        else:
            self._logger = (
                logger if logger is not None else logging.getLogger("dankmemer")
            )
        self._session = session
        self._owns_session = False
        self._admission = Admission()
        self._transport: Transport | None = None
        self._state = ClientState.NEW
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._event_handlers: dict[str, int] = {}

        clock = self._admission.clock
        self._coordinator = (
            Coordinator(
                cast(CoordinatedEventStore, event_store),
                coordination,
                events,
                policies,
                DEPENDENCIES,
                token,
                clock,
                self._logger,
            )
            if coordination.mode is CoordinationMode.SHARED
            else None
        )
        self._admission.shared = self._coordinator
        self._events = EventClient(
            self._request_json,
            policies,
            events,
            clock=clock,
            logger=self._logger,
            startup_jitter=polling.startup_jitter_seconds,
            store=event_store,
            coordinator=self._coordinator,
        )
        guard = self._check_resource_access
        reference = TTLCache(ttl=timedelta(hours=1))
        uncached = NoCache()
        fishing = cache.fishing if cache.fishing is not None else FishingCacheConfig()
        self.items = Items(
            self._request_json,
            cache=_cache_policy(cache.items, reference),
            clock=clock,
            guard=guard,
        )
        self.pets = Pets(
            self._request_json,
            cache=_cache_policy(cache.pets, reference),
            clock=clock,
            guard=guard,
        )
        self.skins = Skins(
            self._request_json,
            cache=_cache_policy(cache.skins, reference),
            clock=clock,
            guard=guard,
        )
        self.commands = Commands(
            self._request_json,
            cache=_cache_policy(cache.commands, reference),
            clock=clock,
            guard=guard,
        )
        self.fishing = Fishing(
            self._request_json,
            clock=clock,
            guard=guard,
            cache=FishingCacheConfig(
                creatures=_cache_policy(fishing.creatures, reference),
                locations=_cache_policy(fishing.locations, reference),
                npcs=_cache_policy(fishing.npcs, reference),
                tools=_cache_policy(fishing.tools, reference),
                baits=_cache_policy(fishing.baits, reference),
                buckets=_cache_policy(fishing.buckets, reference),
                skills=_cache_policy(fishing.skills, reference),
            ),
        )
        self.blogs = Blogs(
            self._request_json,
            cache=_cache_policy(cache.blogs, uncached),
            clock=clock,
            guard=guard,
        )
        self.changelogs = Changelogs(
            self._request_json,
            cache=_cache_policy(cache.changelogs, uncached),
            clock=clock,
            guard=guard,
        )
        self.global_boosts = GlobalBoosts(
            self._request_json,
            cache=_cache_policy(cache.global_boosts, uncached),
            clock=clock,
            guard=guard,
        )
        self.lottery = Lottery(
            self._request_json,
            cache=_cache_policy(cache.lottery, uncached),
            clock=clock,
            guard=guard,
        )
        self.merchant_trades = MerchantTrades(
            self._request_json,
            items=self.items,
            baits=self.fishing.baits,
            cache=_cache_policy(cache.merchant_trades, uncached),
            clock=clock,
            guard=guard,
        )
        self.stream = Stream(
            self._request_json,
            cache=_cache_policy(cache.stream_trending_game, uncached),
            clock=clock,
            guard=guard,
        )
        self.users = Users(self._request_json, clock=clock, guard=guard)
        self.drops = Drops(
            self._request_json,
            cache=_cache_policy(cache.drops, uncached),
            clock=clock,
            guard=guard,
        )
        self.store_sales = StoreSales(
            self._request_json,
            cache=_cache_policy(cache.store_sales, uncached),
            clock=clock,
            guard=guard,
        )
        self.store_daily_gifts = StoreDailyGifts(
            self._request_json,
            items=self.items,
            cache=_cache_policy(cache.store_daily_gifts, uncached),
            clock=clock,
            guard=guard,
        )
        self.fishing_events = FishingEvents(
            self._request_json,
            cache=_cache_policy(cache.fishing_events, uncached),
            clock=clock,
            guard=guard,
        )
        self._resources: tuple[Resource, ...] = (
            self.items,
            self.pets,
            self.skins,
            self.commands,
            self.fishing.creatures,
            self.fishing.locations,
            self.fishing.npcs,
            self.fishing.tools,
            self.fishing.baits,
            self.fishing.buckets,
            self.fishing.skills,
            self.blogs,
            self.changelogs,
            self.global_boosts,
            self.lottery,
            self.merchant_trades,
            self.stream,
            self.users,
            self.drops,
            self.store_sales,
            self.store_daily_gifts,
            self.fishing_events,
        )

    @property
    def state(self) -> ClientState:
        """The client's current lifecycle state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Whether the client has been started and remains open."""
        return self._state is ClientState.RUNNING

    @property
    def is_closed(self) -> bool:
        """Whether the client has finished closing."""
        return self._state is ClientState.CLOSED

    @property
    def active_polling_resources(self) -> frozenset[PollingResource]:
        """Resources required by listeners and enabled in the polling settings.

        Before startup this shows demand; polling begins only after ``start``.
        Several listeners for the same resource share one polling worker.
        A resource remains in this set when polling is paused after a failure;
        :attr:`paused_polling_resources` identifies those resources.
        Shared mode includes saved subscriptions belonging to other clients;
        one client owns the polling for each resource at a time.
        """
        return self._events.scheduler.active_resources

    @property
    def paused_polling_resources(self) -> frozenset[PollingResource]:
        """Resources paused after an HTTP 400, 401, or 403 polling response.

        Listeners and their last accepted baseline are retained. Fix the cause
        shown in :attr:`last_poll_errors`, then call :meth:`retry_polling`.
        In shared mode this pause belongs to this client, not the shared store.
        Saved durable callbacks can still be delivered while polling is paused.
        """
        return self._events.scheduler.paused_resources

    def retry_polling(self, resource: PollingResource) -> None:
        """Resume automatic polling after correcting a resource's HTTP failure.

        The client must be running and automatic polling must be enabled for
        ``resource``. Pass a :class:`PollingResource` member. An unpaused
        resource is left unchanged. The next attempt still waits for request
        spacing and allowance, and its error clears only after a successful
        poll. Adding or replacing listeners does not resume paused polling.
        """
        self._check_listener_access()
        self._events.scheduler.retry_polling(resource)

    async def count_pending_events(self, *, subscription_id: str | None = None) -> int:
        """Count unacknowledged calls in memory or in the supplied store.

        With no subscription ID this includes all calls in the store, even
        for listeners that are no longer registered. A running call remains
        pending until its acknowledgement is saved.
        """
        self._check_resource_access()
        return await self._events.count_pending(subscription_id)

    @property
    def paused_event_subscriptions(self) -> frozenset[str]:
        """Registered subscriptions paused after delivery or store failures."""
        return self._events.paused_subscriptions

    @property
    def last_delivery_errors(self) -> Mapping[str, Exception]:
        """The most recent delivery errors, keyed by stable subscription ID.

        Durable failures retain their calls and pause their subscription.
        A successfully acknowledged retry clears its error. Best-effort
        callback failures are logged and discarded rather than paused.
        """
        return self._events.delivery_errors

    @property
    def last_coordination_error(self) -> Exception | None:
        """The latest failed shared renewal or background subscription update.

        A successful coordination operation clears this error. Independent
        mode returns ``None``. Explicit subscription sync errors are raised
        to the caller.
        """
        return None if self._coordinator is None else self._coordinator.last_error

    async def sync_event_subscriptions(self) -> None:
        """Save shared listener registrations before returning.

        Shared mode saves runtime additions and removals in the background.
        Await this method when another process must see them before proceeding.
        It can be called before :meth:`start` and makes no API requests.
        Independent mode raises ``ConfigurationError``.
        """
        self._check_resource_access()
        await self._events.sync_subscriptions()

    def retry_pending(self, subscription_id: str) -> None:
        """Resume a registered durable subscription after addressing its failure.

        The client must be running. Calls are retried in their saved order;
        this operation does not request new API data. Use the subscription ID
        passed when registering the listener, not its numeric listener ID.
        In shared mode this resumes the ID across clients. The database update
        runs in the background; :meth:`sync_event_subscriptions` waits for it.
        """
        self._check_listener_access()
        self._events.retry_pending(subscription_id)

    @property
    def last_poll_errors(self) -> Mapping[PollingResource, Exception]:
        """Errors from the latest failed poll cycles, as a read-only mapping.

        A successful cycle clears that resource's error. Poll failures are
        also logged and retain its previous baseline. Inspect this mapping
        to detect publication recovery gaps or exhausted memory bounds.
        HTTP 400, 401, and 403 pause automatic polling until the cause is fixed
        and :meth:`retry_polling` is called.
        """
        return self._events.errors

    def _check_listener_access(self) -> None:
        if self._state in (ClientState.CLOSING, ClientState.CLOSED):
            raise LifecycleError("client is closing or closed")
        if self._loop is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                raise LifecycleError(
                    "listener changes require the client's event loop"
                ) from None
            if loop is not self._loop:
                raise LifecycleError("client is bound to a different event loop")

    def _event_name(self, callback: _Listener, name: str | None) -> str:
        value: object = getattr(callback, "__name__", None) if name is None else name
        if not isinstance(value, str):
            raise ConfigurationError("listener requires an explicit event name")
        event = value.removeprefix("on_")
        if event not in DEPENDENCIES:
            raise ConfigurationError("unknown event name")
        return event

    def add_listener(
        self,
        callback: _Listener,
        /,
        *,
        name: str | None = None,
        subscription_id: str | None = None,
    ) -> int:
        """Register an async callback and return its listener ID.

        ``name`` accepts an event name with or without ``on_``. If omitted,
        the callback's name is used. Registering the same callback for the
        same event again returns the existing ID. A running client begins
        polling newly needed resources, subject to the configured schedule.
        Durable delivery requires a stable ``subscription_id``. Reuse that
        ID after a restart to replay pending calls. It must identify only one
        event and listener in the store.
        """
        self._check_listener_access()
        return self._events.add_listener(
            self._event_name(callback, name), callback, subscription_id=subscription_id
        )

    def remove_listener(
        self, listener: int | _Listener, /, *, name: str | None = None
    ) -> bool:
        """Remove a listener by ID or callback, returning whether it was registered.

        Pass the ID returned by :meth:`add_listener`, or the callback
        registered with :meth:`event` or :meth:`listen`. For a callback,
        ``name`` selects its event and defaults to the callback's name.
        Bound methods can be removed using a fresh access to the same method.

        Best-effort queued calls are discarded; a running call may finish.
        Durable calls remain saved and a running call can be interrupted.
        Register its subscription ID again to resume pending calls.
        Removing the last listener for a resource stops future polls.
        A request already in progress can finish.
        """
        self._check_listener_access()
        listener_id: int | None
        if isinstance(listener, int):
            if type(listener) is not int or listener <= 0:
                raise ConfigurationError("listener ID must be a positive integer")
            if name is not None:
                raise ConfigurationError(
                    "name is only supported when removing a callback"
                )
            listener_id = listener
        elif callable(listener):
            listener_id = self._events.find_listener(
                self._event_name(listener, name), listener
            )
            if listener_id is None:
                return False
        else:
            raise ConfigurationError("listener must be an ID or async callback")
        removed = self._events.remove_listener(listener_id)
        for event, registered in tuple(self._event_handlers.items()):
            if registered == listener_id:
                del self._event_handlers[event]
        return removed

    @overload
    def event(
        self, callback: _Callback, /, *, subscription_id: str | None = None
    ) -> _Callback: ...

    @overload
    def event(
        self, /, *, subscription_id: str | None = None
    ) -> Callable[[_Callback], _Callback]: ...

    def event(
        self,
        callback: _Callback | None = None,
        /,
        *,
        subscription_id: str | None = None,
    ) -> _Callback | Callable[[_Callback], _Callback]:
        """Register the primary async handler named ``on_<event>``.

        Use as a decorator. Registering another primary handler for the same
        event replaces the earlier handler. Durable calls remain saved.
        Additional handlers registered through :meth:`listen` are retained.
        The decorated function and its type are returned unchanged.
        The default subscription ID is ``on_<event>``. Pass an explicit ID
        with ``@dank.event(subscription_id="...")`` to name the consumer.
        """
        if callback is None:

            def decorator(listener: _Callback) -> _Callback:
                return self.event(listener, subscription_id=subscription_id)

            return decorator
        self._check_listener_access()
        event = self._event_name(callback, None)
        previous = self._event_handlers.get(event)
        listener_id = self._events.add_listener(
            event,
            callback,
            subscription_id=subscription_id
            if subscription_id is not None
            else f"on_{event}",
            replace_id=previous,
        )
        self._event_handlers[event] = listener_id
        return callback

    def listen(
        self, name: str | None = None, *, subscription_id: str | None = None
    ) -> Callable[[_Callback], _Callback]:
        """Decorate an additional async listener without replacing others.

        Pass an event name or let the callback's name select it. The
        decorated function and its type are returned unchanged.
        Durable delivery requires a stable ``subscription_id``; a function
        name is not used as the identity of an additional listener.
        """

        def decorator(callback: _Callback) -> _Callback:
            self.add_listener(callback, name=name, subscription_id=subscription_id)
            return callback

        return decorator

    def _bind_listeners(
        self, listeners: Sequence[tuple[_Listener, str, str | None, bool]]
    ) -> tuple[int, ...]:
        self._check_listener_access()
        subscriptions: set[str] = set()
        primaries: set[str] = set()
        callbacks: list[tuple[str, _Listener]] = []
        # Check the entire Cog before replacing any existing primary handlers.
        for callback, event, subscription_id, primary in listeners:
            selected_id = (
                f"on_{event}"
                if primary and subscription_id is None
                else subscription_id
            )
            previous = self._event_handlers.get(event) if primary else None
            if (
                self._events.validate_listener(
                    event, callback, subscription_id=selected_id, replace_id=previous
                )
                is not None
            ):
                raise ConfigurationError("decorated Cog listener is already registered")
            if selected_id is not None:
                if selected_id in subscriptions:
                    raise ConfigurationError(
                        "Cog listeners need distinct subscription_ids"
                    )
                subscriptions.add(selected_id)
            if primary:
                if event in primaries:
                    raise ConfigurationError(
                        "Cog has multiple primary handlers for an event"
                    )
                primaries.add(event)
            if any(
                existing_event == event and same_callback(existing, callback)
                for existing_event, existing in callbacks
            ):
                raise ConfigurationError(
                    "Cog listener is marked twice for the same event"
                )
            callbacks.append((event, callback))

        listener_ids: list[int] = []
        for callback, event, subscription_id, primary in listeners:
            if primary:
                self.event(callback, subscription_id=subscription_id)
                listener_ids.append(self._event_handlers[event])
            else:
                listener_ids.append(
                    self.add_listener(
                        callback, name=event, subscription_id=subscription_id
                    )
                )
        return tuple(listener_ids)

    def clear_cache(self) -> None:
        """Discard cached data for every resource.

        The next access reloads data as needed. Requests already in progress
        can finish for their callers but cannot refill a cleared cache.
        """
        for resource in self._resources:
            resource.clear_cache()

    def _check_resource_access(self) -> None:
        self._bind_loop()
        if self._state in (ClientState.CLOSING, ClientState.CLOSED):
            raise LifecycleError("client is closing or closed")

    def _bind_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._lifecycle_lock = asyncio.Lock()
        elif self._loop is not loop:
            raise LifecycleError("client is bound to a different event loop")
        assert self._lifecycle_lock is not None
        return self._lifecycle_lock

    def _initialize_session_unlocked(self) -> aiohttp.ClientSession:
        session = self._session
        if session is not None:
            if session.closed:
                raise LifecycleError("supplied session is closed")
            if session._loop is not asyncio.get_running_loop():  # pyright: ignore[reportPrivateUsage]
                raise LifecycleError("supplied session belongs to another event loop")
        else:
            session = aiohttp.ClientSession()
            self._session = session
            self._owns_session = True
        if self._transport is None:
            self._transport = Transport(
                session,
                self._token,
                origin=self._origin,
                application=self._application,
                request=self._request,
                retry=self._retry,
                admission=self._admission,
            )
        return session

    async def _ensure_session(self) -> aiohttp.ClientSession:
        lock = self._bind_loop()
        async with lock:
            if self._state in (ClientState.CLOSING, ClientState.CLOSED):
                raise LifecycleError("client is closing or closed")
            return self._initialize_session_unlocked()

    async def _request_json(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        lock = self._bind_loop()
        async with lock:
            if self._state in (ClientState.CLOSING, ClientState.CLOSED):
                raise LifecycleError("client is closing or closed")
            self._initialize_session_unlocked()
            transport = self._transport
            assert transport is not None
        return await transport.get(
            route,
            automatic=automatic,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
            category=category,
        )

    async def start(self) -> None:
        """Prepare the HTTP session and start polling resources with listeners."""
        lock = self._bind_loop()
        async with lock:
            if self._state is ClientState.RUNNING:
                return
            if self._state in (ClientState.CLOSING, ClientState.CLOSED):
                raise LifecycleError("a closed client cannot restart")
            self._state = ClientState.STARTING
            try:
                await self._events.prepare()
                self._initialize_session_unlocked()
                await self._events.start()
            except BaseException:
                self._state = ClientState.NEW
                raise
            self._state = ClientState.RUNNING

    async def _wait_for_in_flight(
        self, caller: asyncio.Task[object] | None, deadline: float
    ) -> None:
        pending: set[asyncio.Future[None] | asyncio.Task[object]] = {
            task
            for task in self._events.delivery_tasks
            if task is not caller and not task.done()
        }
        pending.update(
            task
            for resource in self._resources
            for task in resource._active_loaders  # pyright: ignore[reportPrivateUsage]
            if task is not caller and not task.done()
        )
        if self._transport is not None:
            pending.update(self._transport.request_completions)
        remaining = deadline - asyncio.get_running_loop().time()
        if pending and remaining > 0:
            # A request can finish before its caller's application task finishes.
            await asyncio.wait(pending, timeout=remaining)

    async def _finish_close(
        self, caller: asyncio.Task[object] | None, deadline: float | None
    ) -> None:
        try:
            async with AsyncExitStack() as cleanup:
                # Reverse registration closes callbacks before their HTTP session.
                cleanup.push_async_callback(self._close_owned_session)
                if self._transport is not None:
                    cleanup.push_async_callback(self._transport.close)
                cleanup.push_async_callback(self._close_resources)
                cleanup.push_async_callback(self._events.close, caller=caller)
                await self._events.stop(caller=caller)
                if deadline is not None:
                    await self._wait_for_in_flight(caller, deadline)
        finally:
            self._transport = None
            self._session = None
            self._owns_session = False
            self._token = ""
            self._event_handlers.clear()
            self._state = ClientState.CLOSED

    async def _close_resources(self) -> None:
        await asyncio.gather(
            *(resource._close() for resource in self._resources)  # pyright: ignore[reportPrivateUsage]
        )

    async def _close_owned_session(self) -> None:
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()

    async def close(self, *, grace_seconds: float | None = None) -> None:
        """Stop event workers, cancel requests, and close an owned session.

        Polling stops and new resource calls are rejected as shutdown begins.
        With a positive ``grace_seconds``, running manual requests and callbacks have
        up to that many seconds to finish. The default, ``None``, and zero
        cancel them immediately. The first concurrent close call sets the
        grace period; cancelling a caller does not interrupt shared cleanup.

        Queued calls are not started during this wait. Unfinished best-effort
        calls are discarded; durable calls remain in the store for replay.
        Cancellation and session cleanup follow the grace period and may
        take additional time. A callback that initiates closing is not waited
        on or cancelled; it can finish after shutdown.

        The client does not close a supplied session or store. Keep the store
        open until every callback that initiates closing has returned.
        """
        if grace_seconds is not None:
            _number(grace_seconds, "grace_seconds", minimum=0, strict=False)
        if self._state is ClientState.CLOSED:
            if self._coordinator is not None:
                await self._coordinator.close(
                    caller=cast(asyncio.Task[object] | None, asyncio.current_task())
                )
            return
        lock = self._bind_loop()
        async with lock:
            if self._close_task is None:
                self._state = ClientState.CLOSING
                caller = cast(asyncio.Task[object] | None, asyncio.current_task())
                deadline = (
                    asyncio.get_running_loop().time() + grace_seconds
                    if grace_seconds is not None and grace_seconds > 0
                    else None
                )
                self._close_task = asyncio.create_task(
                    self._finish_close(caller, deadline)
                )
            task = self._close_task
        # Cancelling one caller must not cancel the shared close task.
        await asyncio.shield(task)

    async def __aenter__(self) -> Self:
        try:
            await self.start()
        except BaseException:
            # Python does not call __aexit__ when entering the context fails.
            try:
                await self.close()
            except Exception:
                self._logger.exception("client cleanup failed after a startup error")
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.close()
        except Exception:
            if exc is None:
                raise
            # Keep the context body's error as the one the caller sees.
            self._logger.exception("client cleanup failed after an application error")
