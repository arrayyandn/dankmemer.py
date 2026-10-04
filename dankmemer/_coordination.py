from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Coroutine, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import TypeVar, cast
from uuid import uuid4

from ._clock import Clock
from ._coordination_storage import CoordinatedEventStore, SharedCallbacks, SharedCheck
from ._parsing import Record
from ._storage import CallbackIntent, CommitResult
from .config import (
    CoordinationConfig,
    DailyPolling,
    EventConfig,
    HourlyPolling,
    IntervalPolling,
)
from .enums import PollingResource
from .errors import (
    ConfigurationError,
    DankMemerResponseError,
    EventPayloadError,
    LifecycleError,
)
from .http._budget import RequestBudget, integer_header

_T = TypeVar("_T")
Policy = IntervalPolling | DailyPolling | HourlyPolling


class LeaseLost(LifecycleError):
    """The database no longer recognises this client's ownership."""


@dataclass(frozen=True, slots=True)
class Lease:
    key: str
    token: str


@dataclass(slots=True)
class _Owner:
    owner: str
    token: str
    expires_at: float


@dataclass(slots=True)
class _Member:
    key_id: str
    expires_at: float
    subscriptions: tuple[str, ...]


@dataclass(slots=True)
class _Subscription:
    event: str
    resource: str
    enabled: bool = True
    paused: bool = False


@dataclass(slots=True)
class _State:
    application_id: str
    settings: str
    budget: RequestBudget
    members: dict[str, _Member] = field(default_factory=lambda: dict[str, _Member]())
    leases: dict[str, _Owner] = field(default_factory=lambda: dict[str, _Owner]())
    subscriptions: dict[str, _Subscription] = field(
        default_factory=lambda: dict[str, _Subscription]()
    )
    policies: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    poll_after: dict[str, float] = field(default_factory=lambda: dict[str, float]())
    key_limits: dict[str, dict[str, int]] = field(
        default_factory=lambda: dict[str, dict[str, int]]()
    )
    pending_minute: int = 0
    pending_day: int = 0
    minute_reset: float = 0.0
    day_reset: float = 0.0

    def prune(self, now: float) -> None:
        self.members = {
            key: member
            for key, member in self.members.items()
            if member.expires_at > now
        }
        self.leases = {
            key: owner for key, owner in self.leases.items() if owner.expires_at > now
        }
        active_keys = {member.key_id for member in self.members.values()}
        self.key_limits = {
            key: limits for key, limits in self.key_limits.items() if key in active_keys
        }
        if now >= self.minute_reset:
            self.pending_minute = 0
        if now >= self.day_reset:
            self.pending_day = 0

    def dump(self) -> bytes:
        return json.dumps(
            {
                "format": "dankmemer.py/coordination",
                "version": 1,
                "application": self.application_id,
                "settings": self.settings,
                "budget": self.budget.dump(),
                "members": {key: asdict(value) for key, value in self.members.items()},
                "leases": {key: asdict(value) for key, value in self.leases.items()},
                "subscriptions": {
                    key: asdict(value) for key, value in self.subscriptions.items()
                },
                "policies": {
                    key: {"signature": value} for key, value in self.policies.items()
                },
                "poll_after": {
                    key: {"time": value} for key, value in self.poll_after.items()
                },
                "key_limits": self.key_limits,
                "pending_minute": self.pending_minute,
                "pending_day": self.pending_day,
                "minute_reset": self.minute_reset,
                "day_reset": self.day_reset,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def load(cls, payload: bytes) -> _State:
        try:
            value: object = json.loads(payload)
            record = Record(value, path="event_store/coordination", field="")
            if (
                record.string("format") != "dankmemer.py/coordination"
                or record.integer("version") != 1
            ):
                raise EventPayloadError("unsupported shared coordination payload")
            state = cls(
                record.string("application"),
                record.string("settings"),
                RequestBudget.load(record.record("budget")),
            )
            state.members = dict(
                record.mapping(
                    "members",
                    lambda entry: _Member(
                        entry.string("key_id"),
                        entry.number("expires_at"),
                        entry.strings("subscriptions"),
                    ),
                )
            )
            state.leases = dict(
                record.mapping(
                    "leases",
                    lambda entry: _Owner(
                        entry.string("owner"),
                        entry.string("token"),
                        entry.number("expires_at"),
                    ),
                )
            )
            state.subscriptions = dict(
                record.mapping(
                    "subscriptions",
                    lambda entry: _Subscription(
                        entry.string("event"),
                        entry.string("resource"),
                        entry.boolean("enabled"),
                        entry.boolean("paused"),
                    ),
                )
            )
            state.policies = dict(
                record.mapping("policies", lambda entry: entry.string("signature"))
            )
            state.poll_after = dict(
                record.mapping("poll_after", lambda entry: entry.number("time"))
            )
            state.key_limits = {
                key: {"Minute": entry.integer("Minute"), "Day": entry.integer("Day")}
                for key, entry in record.mapping(
                    "key_limits", lambda entry: entry
                ).items()
            }
            state.pending_minute = record.integer("pending_minute")
            state.pending_day = record.integer("pending_day")
            state.minute_reset = record.number("minute_reset")
            state.day_reset = record.number("day_reset")
            if min(state.pending_minute, state.pending_day) < 0:
                raise EventPayloadError("invalid shared request reservation counts")
            return state
        except (ValueError, UnicodeError, DankMemerResponseError) as error:
            raise EventPayloadError("invalid shared coordination data") from error


def independent_store(payload: bytes | None, now: float) -> tuple[bytes, None]:
    if payload:
        raise ConfigurationError("this event store is bound to shared coordination")
    return b"", None


class Coordinator:
    """Own pollers, subscriptions, and request allowances through one store."""

    def __init__(
        self,
        store: CoordinatedEventStore,
        config: CoordinationConfig,
        events: EventConfig,
        policies: Mapping[PollingResource, Policy],
        dependencies: Mapping[str, PollingResource],
        token: str,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self._logger = logger
        assert config.application_id is not None
        self._application_id = config.application_id
        self._owner = uuid4().hex
        self._key_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self._settings = json.dumps(
            {
                "emit_initial": events.emit_initial,
                "max_pending": events.max_pending_deliveries,
                "publication_page_size": events.publication_page_size,
                "max_staged": events.max_staged_publications,
                "timestamp_tolerance": events.timestamp_tolerance.total_seconds(),
            },
            sort_keys=True,
        )
        self._policies = {
            resource.value: json.dumps(
                {"seconds": policy.interval.total_seconds()}
                if isinstance(policy, IntervalPolling)
                else {name: float(value) for name, value in asdict(policy).items()},
                sort_keys=True,
            )
            for resource, policy in policies.items()
        }
        self._dependencies = dict(dependencies)
        self._desired: dict[str, str] = {}
        self._removed: set[str] = set()
        self._owned: dict[str, tuple[Lease, asyncio.Task[object] | None]] = {}
        self._state: _State | None = None
        self._wake = asyncio.Event()
        self._prepared = False
        self._closing = False
        self._closed = False
        self._runner: asyncio.Task[None] | None = None
        self._operations: set[asyncio.Task[None]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._close_caller: asyncio.Task[object] | None = None
        self._reservation: ContextVar[tuple[float, float] | None] = ContextVar(
            "shared_request_reservation", default=None
        )
        self._polling: ContextVar[Lease | None] = ContextVar(
            "shared_poller", default=None
        )
        self.on_change: Callable[[], None] | None = None
        self.last_error: Exception | None = None

    def _read(self, payload: bytes | None, now: float) -> _State:
        state = (
            _State.load(payload)
            if payload
            else _State(self._application_id, self._settings, RequestBudget(now))
        )
        if state.application_id != self._application_id:
            raise ConfigurationError("event store belongs to another API application")
        if state.settings != self._settings:
            raise ConfigurationError(
                "shared clients must use matching EventConfig settings"
            )
        for subscription in state.subscriptions.values():
            if self._dependencies.get(subscription.event) is not PollingResource(
                subscription.resource
            ):
                raise EventPayloadError(
                    "shared subscription has an unsupported event binding"
                )
        return state

    async def _update(self, operation: Callable[[_State, float], _T]) -> _T:
        def update(
            payload: bytes | None, now: float
        ) -> tuple[bytes, tuple[_State, _T]]:
            state = self._read(payload, now)
            state.prune(now)
            result = operation(state, now)
            return state.dump(), (state, result)

        state, result = await self.store.coordinate(update)
        self._state = state
        self.last_error = None
        return result

    def register(self, subscription_id: str, event: str) -> None:
        self._desired[subscription_id] = event
        self._removed.discard(subscription_id)
        self._wake.set()

    def unregister(self, subscription_id: str) -> None:
        self._desired.pop(subscription_id, None)
        self._removed.add(subscription_id)
        self._wake.set()

    async def prepare(self) -> None:
        if self._closed or self._closing:
            raise LifecycleError("shared coordinator is closing")
        desired = dict(self._desired)
        removed = set(self._removed)

        def mirror(state: _State, now: float) -> None:
            for resource, policy in self._policies.items():
                saved = state.policies.setdefault(resource, policy)
                if saved != policy:
                    raise ConfigurationError(
                        f"shared polling settings differ for {resource}"
                    )
            state.members[self._owner] = _Member(
                self._key_id, now + self.config.lease_seconds, tuple(desired)
            )
            state.key_limits.setdefault(self._key_id, {"Minute": 60, "Day": 10_000})
            state.budget.update_headers(
                now,
                {
                    f"X-RateLimit-Limit-{suffix}": str(
                        min(limits[suffix] for limits in state.key_limits.values())
                    )
                    for suffix in ("Minute", "Day")
                },
            )
            for subscription_id, event in desired.items():
                resource = self._dependencies[event].value
                existing = state.subscriptions.get(subscription_id)
                if existing is not None and (
                    existing.event != event or existing.resource != resource
                ):
                    raise ConfigurationError(
                        "subscription_id is already bound to another event"
                    )
                if existing is None:
                    state.subscriptions[subscription_id] = _Subscription(
                        event, resource
                    )
                else:
                    existing.enabled = True
            for subscription_id in removed:
                if not any(
                    subscription_id in member.subscriptions
                    for member in state.members.values()
                ):
                    if (
                        subscription := state.subscriptions.get(subscription_id)
                    ) is not None:
                        subscription.enabled = False
                removed_owner = state.leases.get("subscription:" + subscription_id)
                owned = self._owned.get("subscription:" + subscription_id)
                # A self-removing callback still needs to acknowledge its call.
                active = (
                    owned is not None and owned[1] is not None and not owned[1].done()
                )
                if (
                    removed_owner is not None
                    and removed_owner.owner == self._owner
                    and not active
                ):
                    state.leases.pop("subscription:" + subscription_id)
            for key, (lease, task) in self._owned.items():
                stored = state.leases.get(key)
                if (
                    stored is not None
                    and stored.owner == self._owner
                    and stored.token == lease.token
                ):
                    if (
                        key.startswith("subscription:")
                        and key.removeprefix("subscription:") not in desired
                        and (task is None or task.done())
                    ):
                        state.leases.pop(key)
                    else:
                        stored.expires_at = now + self.config.lease_seconds

        await self._update(mirror)
        self._removed.difference_update(removed)
        self._prepared = True
        self._check_owners()
        if self.on_change is not None:
            self.on_change()

    def _check_owners(self) -> None:
        if self._state is None:
            return
        for key, (lease, task) in tuple(self._owned.items()):
            stored = self._state.leases.get(key)
            if (
                stored is None
                or stored.owner != self._owner
                or stored.token != lease.token
            ):
                self._owned.pop(key)
                if task is not None and not task.done():
                    task.cancel()

    @property
    def resources(self) -> frozenset[PollingResource]:
        if self._state is None:
            return frozenset()
        return frozenset(
            PollingResource(subscription.resource)
            for subscription in self._state.subscriptions.values()
            if subscription.enabled
        )

    def paused(self, subscription_id: str) -> bool:
        if self._state is None:
            return False
        subscription = self._state.subscriptions.get(subscription_id)
        return subscription is not None and subscription.paused

    def owns(self, key: str) -> bool:
        return key in self._owned

    def forget(self, lease: Lease) -> None:
        owned = self._owned.get(lease.key)
        if owned is not None and owned[0] == lease:
            self._owned.pop(lease.key)

    async def validate(self, lease: Lease) -> None:
        await self._update(lambda state, now: self._check(state, now, lease))

    @contextmanager
    def polling(self, lease: Lease) -> Generator[None, None, None]:
        context = self._polling.set(lease)
        try:
            yield
        finally:
            self._polling.reset(context)

    def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._closed:
            self._wake.clear()
            try:
                await self.prepare()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = error
                # A failed renewal cannot be treated as continuing ownership.
                for _, task in self._owned.values():
                    if task is not None and not task.done():
                        task.cancel()
                self._owned.clear()
                self._logger.exception("could not renew shared coordination")
            sleeper = asyncio.create_task(
                self.clock.sleep(self.config.heartbeat_seconds)
            )
            notifier = asyncio.create_task(self._wake.wait())
            try:
                await asyncio.wait(
                    (sleeper, notifier), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in (sleeper, notifier):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(sleeper, notifier, return_exceptions=True)

    def _claim(self, state: _State, now: float, key: str) -> Lease | None:
        stored = state.leases.get(key)
        if stored is not None and stored.owner != self._owner:
            return None
        if stored is None:
            stored = _Owner(self._owner, uuid4().hex, now + self.config.lease_seconds)
            state.leases[key] = stored
        return Lease(key, stored.token)

    def _track(self, lease: Lease) -> None:
        self._owned[lease.key] = (
            lease,
            cast(asyncio.Task[object] | None, asyncio.current_task()),
        )

    def _check(self, state: _State, now: float, lease: Lease) -> None:
        stored = state.leases.get(lease.key)
        if (
            stored is None
            or stored.owner != self._owner
            or stored.token != lease.token
            or stored.expires_at <= now
        ):
            raise LeaseLost("shared ownership expired or moved to another client")

    async def claim_poll(self, resource: PollingResource) -> tuple[Lease | None, bool]:
        def claim(state: _State, now: float) -> tuple[Lease | None, bool]:
            if not any(
                subscription.enabled and subscription.resource == resource.value
                for subscription in state.subscriptions.values()
            ):
                return None, False
            lease = self._claim(state, now, "resource:" + resource.value)
            due = lease is not None and state.poll_after.get(resource.value, 0) <= now
            if due:
                state.poll_after[resource.value] = now + 60.0
            return lease, due

        lease, due = await self._update(claim)
        if lease is not None:
            self._track(lease)
        return lease, due

    async def finish_poll(
        self, resource: PollingResource, lease: Lease, delay: Callable[[float], float]
    ) -> None:
        def finish(state: _State, now: float) -> None:
            self._check(state, now, lease)
            state.poll_after[resource.value] = max(
                state.poll_after.get(resource.value, 0), now + max(60.0, delay(now))
            )

        await self._update(finish)

    async def claim_subscription(self, subscription_id: str) -> Lease | None:
        def claim(state: _State, now: float) -> Lease | None:
            subscription = state.subscriptions.get(subscription_id)
            if subscription is None or not subscription.enabled or subscription.paused:
                return None
            return self._claim(state, now, "subscription:" + subscription_id)

        lease = await self._update(claim)
        if lease is not None:
            self._track(lease)
        return lease

    def callback_guard(self, lease: Lease) -> SharedCheck:
        def check(payload: bytes | None, now: float) -> None:
            self._check(self._read(payload, now), now, lease)

        return check

    def fanout(
        self,
        lease: Lease,
        resource: PollingResource,
        emissions: Sequence[tuple[str, bytes]],
    ) -> SharedCallbacks:
        def callbacks(payload: bytes | None, now: float) -> Sequence[CallbackIntent]:
            state = self._read(payload, now)
            self._check(state, now, lease)
            return tuple(
                CallbackIntent(subscription_id, event, arguments)
                for event, arguments in emissions
                for subscription_id, subscription in state.subscriptions.items()
                if subscription.enabled
                and subscription.resource == resource.value
                and subscription.event == event
            )

        return callbacks

    async def commit(
        self,
        resource: PollingResource,
        lease: Lease,
        *,
        expected_version: int | None,
        payload: bytes,
        revision: int | None,
        emissions: Sequence[tuple[str, bytes]],
        maximum: int | None,
    ) -> CommitResult | None:
        return await self.store.commit_coordinated(
            resource,
            expected_version=expected_version,
            payload=payload,
            revision=revision,
            callbacks=self.fanout(lease, resource, emissions),
            max_pending_callbacks=maximum,
        )

    async def acknowledge(
        self, subscription_id: str, callback_id: int, lease: Lease
    ) -> bool:
        return await self.store.acknowledge_coordinated(
            subscription_id, callback_id, self.callback_guard(lease)
        )

    def pause(self, subscription_id: str, lease: Lease) -> None:
        async def pause() -> None:
            def update(state: _State, now: float) -> None:
                self._check(state, now, lease)
                state.subscriptions[subscription_id].paused = True
                state.leases.pop(lease.key)

            try:
                await self._update(update)
            except LeaseLost:
                return
            self._owned.pop(lease.key, None)
            if self.on_change is not None:
                self.on_change()

        self._spawn_operation(pause())

    def resume(self, subscription_id: str) -> None:
        async def resume() -> None:
            def update(state: _State, now: float) -> None:
                subscription = state.subscriptions.get(subscription_id)
                if subscription is None:
                    raise ConfigurationError("unknown shared subscription_id")
                subscription.paused = False

            await self._update(update)
            if self.on_change is not None:
                self.on_change()
            self._wake.set()

        self._spawn_operation(resume())

    def _spawn_operation(self, operation: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(operation)
        self._operations.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            self._operations.discard(task)
            if not task.cancelled() and (error := task.exception()) is not None:
                self.last_error = (
                    error
                    if isinstance(error, Exception)
                    else LifecycleError("shared operation was interrupted")
                )
                self._logger.error(
                    "shared subscription update failed (%s)", type(error).__name__
                )

        task.add_done_callback(finished)

    async def flush(self) -> None:
        """Wait for already queued pause and retry updates."""
        if self._operations:
            await asyncio.shield(asyncio.gather(*tuple(self._operations)))

    async def acquire(
        self,
        *,
        path: str,
        resource: PollingResource | None,
        automatic: bool,
        deadline: float | None,
    ) -> None:
        if not self._prepared:
            await self.prepare()
        while True:
            if self._closing or self._closed:
                raise LifecycleError("shared coordinator is closing")

            def reserve(
                state: _State, now: float
            ) -> tuple[float | None, tuple[float, float] | None]:
                if automatic and (lease := self._polling.get()) is not None:
                    self._check(state, now, lease)
                monotonic = self.clock.monotonic()
                remaining = None if deadline is None else deadline - monotonic
                wait = state.budget.reserve(
                    now,
                    now,
                    path=path,
                    resource=resource,
                    automatic=automatic,
                    deadline=None if remaining is None else now + remaining,
                )
                if wait == 0:
                    budget = state.budget.dump()
                    minute = cast(dict[str, object], budget["minute"])
                    day = cast(dict[str, object], budget["day"])
                    state.minute_reset = cast(float, minute["reset"])
                    state.day_reset = cast(float, day["reset"])
                    state.pending_minute += 1
                    state.pending_day += 1
                    state.members[self._owner] = _Member(
                        self._key_id,
                        now + self.config.lease_seconds,
                        tuple(self._desired),
                    )
                    return None, (state.minute_reset, state.day_reset)
                return monotonic + wait, None

            wake_at, reservation = await self._update(reserve)
            if wake_at is None:
                self._reservation.set(reservation)
                return
            # The transaction can take time to commit after calculating this wait.
            await self.clock.sleep_until(wake_at)

    async def observe(self, headers: Mapping[str, str]) -> None:
        reservation = self._reservation.get()
        self._reservation.set(None)

        def observe(state: _State, now: float) -> None:
            if reservation is not None:
                if reservation[0] == state.minute_reset:
                    state.pending_minute = max(0, state.pending_minute - 1)
                if reservation[1] == state.day_reset:
                    state.pending_day = max(0, state.pending_day - 1)
            budget = state.budget.dump()
            key_limits = state.key_limits.setdefault(
                self._key_id, {"Minute": 60, "Day": 10_000}
            )
            adjusted = {
                name: value
                for suffix in ("Minute", "Day")
                for part in ("Limit", "Remaining", "Reset")
                if (value := headers.get(name := f"X-RateLimit-{part}-{suffix}"))
                is not None
            }
            for suffix, pending in (
                ("Minute", state.pending_minute),
                ("Day", state.pending_day),
            ):
                limit_name = f"X-RateLimit-Limit-{suffix}"
                remaining_name = f"X-RateLimit-Remaining-{suffix}"
                limit = integer_header(headers.get(limit_name), minimum=1)
                remaining = integer_header(headers.get(remaining_name), minimum=0)
                if limit is not None and (
                    limit <= key_limits[suffix] or remaining is not None
                ):
                    key_limits[suffix] = (
                        min(limit, 10_000) if suffix == "Minute" else limit
                    )
                adjusted[limit_name] = str(
                    min(limits[suffix] for limits in state.key_limits.values())
                )
                if remaining is not None:
                    adjusted[remaining_name] = str(max(0, remaining - pending))
                reset_name = f"X-RateLimit-Reset-{suffix}"
                reset = integer_header(headers.get(reset_name), minimum=0)
                window = cast(dict[str, object], budget[suffix.lower()])
                if reset is not None and reset < cast(float, window["reset"]):
                    adjusted.pop(remaining_name, None)
                    adjusted.pop(limit_name, None)
            state.budget.update_headers(now, adjusted)

        await self._update(observe)

    async def cooldown(self, seconds: float) -> None:
        await self._update(lambda state, now: state.budget.delay(now + seconds))

    async def close(self, *, caller: asyncio.Task[object] | None = None) -> None:
        if self._close_task is not None:
            if (
                caller is self._close_caller
                and caller is not None
                and not caller.done()
            ):
                return
            await asyncio.shield(self._close_task)
            return
        keep_caller = (
            caller is not None
            and any(task is caller for _, task in self._owned.values())
            and not caller.done()
        )
        if keep_caller:
            self._close_caller = caller

            async def later() -> None:
                assert caller is not None
                await asyncio.gather(caller, return_exceptions=True)
                await self._finish_close()

            self._close_task = asyncio.create_task(later())
            self._close_task.add_done_callback(self._close_done)
            return
        self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    def _close_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            self.last_error = (
                error
                if isinstance(error, Exception)
                else LifecycleError("shared cleanup was interrupted")
            )
            self._logger.error(
                "shared coordination cleanup failed (%s)", type(error).__name__
            )

    async def _finish_close(self) -> None:
        self._closing = True
        self._closed = True
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
        if self._operations:
            await asyncio.gather(*self._operations, return_exceptions=True)
        if self._prepared:

            def release(state: _State, now: float) -> None:
                state.members.pop(self._owner, None)
                state.leases = {
                    key: value
                    for key, value in state.leases.items()
                    if value.owner != self._owner
                }

            await self._update(release)
        self._owned.clear()
