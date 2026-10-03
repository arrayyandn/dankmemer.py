from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from ._clock import Clock, SystemClock
from ._coordination import Coordinator, Lease, LeaseLost, independent_store
from ._coordination_storage import CoordinatedEventStore
from ._events import is_async_callback
from ._observations import Emission, Observation, checked_bool, prepare_observation
from ._storage import (
    CallbackIntent,
    EventStore,
    checked_event_name,
    checked_subscription_id,
)
from .enums import PollingResource
from .errors import ConfigurationError, EventPayloadError, LifecycleError

_T = TypeVar("_T")
_Listener = Callable[..., Awaitable[None]]
_Derive = Callable[[_T | None, _T, bool], Sequence[Emission]]


class EventCodec(Protocol[_T]):
    """Round-trip resource snapshots and listener arguments through bytes."""

    def encode_snapshot(self, value: _T) -> bytes: ...

    def decode_snapshot(self, payload: bytes) -> _T: ...

    def encode_args(self, args: tuple[object, ...]) -> bytes: ...

    def decode_args(self, payload: bytes) -> tuple[object, ...]: ...


@dataclass(slots=True)
class _Registration:
    event: str
    callback: _Listener
    wake: asyncio.Event


class StoredEventPipeline(Generic[_T]):
    """Compare observations, commit deliveries, and run named listeners.

    A subscription ID identifies one listener across restarts. A successful
    callback is acknowledged. With durable delivery, a failed or interrupted
    callback stays pending.
    Its subscription pauses after failure until :meth:`retry_pending` is
    called or a new independent pipeline starts. A shared coordinator checks
    poller ownership inside the commit and subscription ownership inside the
    acknowledgement. Shared failures stay paused across process restarts.

    Resource adapters supply the codec and change detection. ``derive`` should
    have no side effects because a competing commit can make it run again.
    This class assumes no particular API payload or revision field.
    ``best_effort=True`` also acknowledges failed callback attempts, so they
    do not replay. The client discards its owned memory store on shutdown.
    """

    def __init__(
        self,
        resource: PollingResource,
        derive: _Derive[_T],
        codec: EventCodec[_T],
        store: EventStore,
        *,
        emit_initial: bool = False,
        logger: logging.Logger | None = None,
        best_effort: bool = False,
        max_pending_callbacks: int | None = None,
        allowed: Callable[[_T, _T], bool] | None = None,
        argument_counts: Mapping[str, int] | None = None,
        coordinator: Coordinator | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._resource = resource
        self._derive = derive
        self._codec = codec
        self._store = store
        self._emit_initial = checked_bool(emit_initial, "emit_initial")
        self._best_effort = checked_bool(best_effort, "best_effort")
        self._max_pending_callbacks = max_pending_callbacks
        self._allowed = allowed
        self._argument_counts = argument_counts
        self._coordinator = coordinator
        self._coordinated_store = (
            store if isinstance(store, CoordinatedEventStore) else None
        )
        self._clock = clock if clock is not None else SystemClock()
        self._poll_lease: Lease | None = None
        self._delivery_leases: dict[str, Lease] = {}
        self._shared_paused: set[str] = set()
        self._logger = (
            logger if logger is not None else logging.getLogger("dankmemer.events")
        )
        self._current: Observation[_T] | None = None
        self._version: int | None = None
        self._registrations: dict[str, _Registration] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._stopping_workers: set[asyncio.Task[None]] = set()
        self._paused: set[str] = set()
        self._errors: dict[str, Exception] = {}
        self._in_flight: dict[str, int] = {}
        self._accept_lock = asyncio.Lock()
        self._running = False
        self._prepared = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def current(self) -> Observation[_T] | None:
        """The last complete observation saved by this pipeline."""
        return self._current

    @property
    def paused_subscriptions(self) -> frozenset[str]:
        return frozenset(self._paused)

    @property
    def delivery_errors(self) -> Mapping[str, Exception]:
        return dict(self._errors)

    def in_flight_id(self, subscription_id: str) -> int | None:
        return self._in_flight.get(subscription_id)

    @property
    def in_flight_ids(self) -> frozenset[int]:
        return frozenset(self._in_flight.values())

    @property
    def delivery_tasks(self) -> frozenset[asyncio.Task[None]]:
        return frozenset(self._workers.values()) | self._stopping_workers

    def stop(self) -> None:
        """Prevent new deliveries while a running callback finishes."""
        self._closed = True
        self._running = False
        for registration in self._registrations.values():
            registration.wake.set()

    def add_listener(
        self, event: str, subscription_id: str, callback: _Listener
    ) -> None:
        """Register a named async listener for delivery when running.

        Use the same subscription ID after a restart to resume delivery. IDs
        must be unique across event resources sharing one store.
        """
        if self._closed:
            raise LifecycleError("event pipeline is closed")
        checked_event_name(event)
        checked_subscription_id(subscription_id)
        if not is_async_callback(callback):
            raise ConfigurationError("event listener must be an async callable")
        if subscription_id in self._registrations:
            raise ConfigurationError("subscription_id is already registered")
        self._registrations[subscription_id] = _Registration(
            event, callback, asyncio.Event()
        )
        if self._running:
            self._ensure_worker(subscription_id)

    def detach_listener(self, subscription_id: str, *, cancel: bool = True) -> bool:
        registration = self._registrations.pop(subscription_id, None)
        if registration is None:
            return False
        registration.wake.set()
        self._paused.discard(subscription_id)
        self._errors.pop(subscription_id, None)
        task = self._workers.get(subscription_id)
        if task is not None:
            self._stopping_workers.add(task)
            if cancel and task is not asyncio.current_task():
                task.cancel()
        return True

    async def remove_listener(self, subscription_id: str) -> bool:
        """Stop a listener without deleting its unacknowledged calls."""
        removed = self.detach_listener(subscription_id)
        task = self._workers.get(subscription_id)
        if removed and task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        return removed

    async def _load_checkpoint(self) -> None:
        checkpoint = await self._store.read_checkpoint(self._resource)
        if checkpoint is None:
            self._current = None
            self._version = None
            return
        if checkpoint.resource is not self._resource or checkpoint.version < 1:
            raise LifecycleError("event store returned an invalid checkpoint")
        value = self._codec.decode_snapshot(checkpoint.payload)
        self._current = Observation(value, complete=True, revision=checkpoint.revision)
        self._version = checkpoint.version

    async def prepare(self) -> None:
        """Load the baseline before any delivery worker starts."""
        async with self._accept_lock:
            if self._closed:
                raise LifecycleError("event pipeline is closed")
            if self._prepared:
                return
            await self._load_checkpoint()
            if self._closed:
                raise LifecycleError("event pipeline is closed")
            self._prepared = True

    async def refresh(self, lease: Lease) -> None:
        """Discard a previous poller's local baseline before taking ownership."""
        async with self._accept_lock:
            await self._load_checkpoint()
            self._poll_lease = lease

    def sync_shared(self) -> None:
        coordinator = self._coordinator
        if coordinator is None:
            return
        for subscription_id, registration in self._registrations.items():
            if coordinator.paused(subscription_id):
                self._shared_paused.add(subscription_id)
                self._paused.add(subscription_id)
                self._errors.setdefault(
                    subscription_id,
                    LifecycleError(
                        "shared subscription is paused; use retry_pending to resume"
                    ),
                )
            elif subscription_id in self._shared_paused:
                self._shared_paused.discard(subscription_id)
                self._paused.discard(subscription_id)
                registration.wake.set()
            self._ensure_worker(subscription_id)

    def activate(self) -> None:
        if self._closed or not self._prepared:
            raise LifecycleError("event pipeline has not been prepared")
        self._running = True
        for subscription_id in self._registrations:
            self._ensure_worker(subscription_id)

    async def start(self) -> None:
        """Load the baseline and resume pending calls for named listeners."""
        await self.prepare()
        self.activate()

    async def accept(self, observation: Observation[_T]) -> bool:
        """Save a complete observation and its listener calls atomically.

        An incomplete or stale observation returns ``False``. A failed encode
        or store commit leaves the baseline unchanged. On a competing commit,
        reload the saved baseline and derive from it again.
        """
        async with self._accept_lock:
            if not self._running or self._closed:
                raise LifecycleError("event pipeline is not running")
            while True:
                if (
                    self._current is not None
                    and self._allowed is not None
                    and not self._allowed(self._current.value, observation.value)
                ):
                    return False
                emissions = prepare_observation(
                    self._current,
                    observation,
                    self._derive,
                    emit_initial=self._emit_initial,
                )
                if emissions is None:
                    return False

                payload = self._codec.encode_snapshot(observation.value)
                intents: list[CallbackIntent] = []
                shared_emissions: list[tuple[str, bytes]] = []
                for emission in emissions:
                    event = checked_event_name(emission.event)
                    if self._coordinator is not None:
                        shared_emissions.append(
                            (event, self._codec.encode_args(emission.args))
                        )
                        continue
                    subscriber_ids = tuple(
                        subscription_id
                        for subscription_id, registration in self._registrations.items()
                        if registration.event == event
                    )
                    if subscriber_ids:
                        arguments = self._codec.encode_args(emission.args)
                        intents.extend(
                            CallbackIntent(subscription_id, event, arguments)
                            for subscription_id in subscriber_ids
                        )
                if self._coordinator is not None:
                    if self._poll_lease is None:
                        raise LeaseLost("poller has no shared ownership")
                    result = await self._coordinator.commit(
                        self._resource,
                        self._poll_lease,
                        expected_version=self._version,
                        payload=payload,
                        revision=observation.revision,
                        emissions=shared_emissions,
                        maximum=self._max_pending_callbacks,
                    )
                elif self._coordinated_store is not None:

                    def independent_callbacks(
                        state: bytes | None,
                        now: float,
                        saved: Sequence[CallbackIntent] = tuple(intents),
                    ) -> Sequence[CallbackIntent]:
                        independent_store(state, now)
                        return saved

                    result = await self._coordinated_store.commit_coordinated(
                        self._resource,
                        expected_version=self._version,
                        payload=payload,
                        revision=observation.revision,
                        callbacks=independent_callbacks,
                        max_pending_callbacks=self._max_pending_callbacks,
                    )
                else:
                    result = await self._store.commit(
                        self._resource,
                        expected_version=self._version,
                        payload=payload,
                        revision=observation.revision,
                        callbacks=intents,
                        max_pending_callbacks=self._max_pending_callbacks,
                    )
                if result is None:
                    previous_version = self._version
                    await self._load_checkpoint()
                    if self._version == previous_version:
                        raise LifecycleError(
                            "event store rejected the current checkpoint"
                        )
                    continue

                self._current = observation
                self._version = result.checkpoint.version
                for subscription_id in {intent.subscription_id for intent in intents}:
                    registration = self._registrations.get(subscription_id)
                    if registration is not None:
                        registration.wake.set()
                        self._ensure_worker(subscription_id)
                return True

    def retry_pending(self, subscription_id: str) -> None:
        """Resume a paused subscription after its failure is addressed."""
        if not self._running or self._closed:
            raise LifecycleError("event pipeline is not running")
        registration = self._registrations.get(subscription_id)
        if registration is None:
            raise ConfigurationError("unknown subscription_id")
        self._paused.discard(subscription_id)
        registration.wake.set()
        self._ensure_worker(subscription_id)

    def _ensure_worker(self, subscription_id: str) -> None:
        if not self._running or subscription_id in self._paused:
            return
        registration = self._registrations.get(subscription_id)
        if registration is None:
            return
        task = self._workers.get(subscription_id)
        if task is not None and not task.done():
            # A replacement must wait for the old worker to finish or cancel.
            return
        task = asyncio.create_task(self._run_deliveries(subscription_id, registration))
        self._workers[subscription_id] = task
        task.add_done_callback(
            lambda finished: self._worker_done(subscription_id, finished)
        )

    def _worker_done(self, subscription_id: str, task: asyncio.Task[None]) -> None:
        if self._workers.get(subscription_id) is not task:
            self._stopping_workers.discard(task)
            return
        del self._workers[subscription_id]
        if task in self._stopping_workers:
            self._stopping_workers.discard(task)
        elif task.cancelled():
            if self._running and (
                self._coordinator is None
                or self._coordinator.owns("subscription:" + subscription_id)
            ):
                self._paused.add(subscription_id)
                self._errors[subscription_id] = LifecycleError(
                    "event delivery worker was cancelled"
                )
                self._logger.error(
                    "event delivery worker cancelled for %s", subscription_id
                )
                self._pause_shared(subscription_id)
        elif self._running and (error := task.exception()) is not None:
            self._paused.add(subscription_id)
            self._errors[subscription_id] = (
                error
                if isinstance(error, Exception)
                else LifecycleError("event delivery worker was interrupted")
            )
            self._logger.error(
                "event delivery worker failed for %s",
                subscription_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            self._pause_shared(subscription_id)
        if self._running and subscription_id not in self._paused:
            self._ensure_worker(subscription_id)

    async def _run_deliveries(
        self, subscription_id: str, registration: _Registration
    ) -> None:
        while (
            self._running and self._registrations.get(subscription_id) is registration
        ):
            registration.wake.clear()
            try:
                if self._coordinator is not None:
                    lease = await self._coordinator.claim_subscription(subscription_id)
                    self.sync_shared()
                    if lease is None:
                        if subscription_id in self._paused:
                            return
                        await self._wait_for_work(registration)
                        continue
                    self._delivery_leases[subscription_id] = lease
                elif self._coordinated_store is not None:
                    await self._coordinated_store.coordinate(independent_store)
                pending = await self._store.pending_callbacks(subscription_id, limit=1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._pause(subscription_id, registration, error)
                self._logger.exception(
                    "could not read pending callbacks for %s", subscription_id
                )
                return
            if not pending:
                if not self._running:
                    return
                await self._wait_for_work(registration)
                continue

            if not self._running:
                return
            record = pending[0]
            if (
                record.resource is not self._resource
                or record.event != registration.event
            ):
                self._pause(
                    subscription_id,
                    registration,
                    EventPayloadError(
                        "pending callback does not match its subscription"
                    ),
                )
                self._logger.error(
                    "pending callback does not match %s", subscription_id
                )
                return
            try:
                arguments = self._codec.decode_args(record.payload)
                if self._argument_counts is not None and len(
                    arguments
                ) != self._argument_counts.get(record.event):
                    raise EventPayloadError(
                        "pending callback has the wrong number of arguments"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._pause(subscription_id, registration, error)
                self._logger.exception(
                    "could not decode callback for %s", subscription_id
                )
                return

            self._in_flight[subscription_id] = record.id
            try:
                if self._coordinator is not None:
                    try:
                        await self._coordinator.validate(
                            self._delivery_leases[subscription_id]
                        )
                    except LeaseLost:
                        self._coordinator_forget(subscription_id)
                        return
                try:
                    await registration.callback(*arguments)
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise
                    if not self._best_effort:
                        self._pause(
                            subscription_id,
                            registration,
                            LifecycleError("event callback raised CancelledError"),
                        )
                        self._logger.error(
                            "event callback cancelled for %s", subscription_id
                        )
                        return
                    self._logger.error(
                        "event callback cancelled for %s", subscription_id
                    )
                except Exception as error:
                    if not self._best_effort:
                        self._pause(subscription_id, registration, error)
                        self._logger.exception(
                            "event callback failed for %s", subscription_id
                        )
                        return
                    self._logger.exception(
                        "event callback failed for %s", subscription_id
                    )

                try:
                    if self._coordinator is not None:
                        acknowledged = await self._coordinator.acknowledge(
                            subscription_id,
                            record.id,
                            self._delivery_leases[subscription_id],
                        )
                    elif self._coordinated_store is not None:

                        def independent_check(state: bytes | None, now: float) -> None:
                            independent_store(state, now)

                        acknowledged = (
                            await self._coordinated_store.acknowledge_coordinated(
                                subscription_id, record.id, independent_check
                            )
                        )
                    else:
                        acknowledged = await self._store.acknowledge(
                            subscription_id, record.id
                        )
                except asyncio.CancelledError:
                    raise
                except LeaseLost:
                    self._coordinator_forget(subscription_id)
                    return
                except Exception as error:
                    self._pause(subscription_id, registration, error)
                    self._logger.exception(
                        "could not acknowledge callback for %s", subscription_id
                    )
                    return
                if not acknowledged:
                    self._pause(
                        subscription_id,
                        registration,
                        LifecycleError(
                            "pending callback disappeared before acknowledgement"
                        ),
                    )
                    self._logger.error(
                        "pending callback disappeared for %s", subscription_id
                    )
                    return
                self._errors.pop(subscription_id, None)
            finally:
                self._in_flight.pop(subscription_id, None)

    def _pause(
        self, subscription_id: str, registration: _Registration, error: Exception
    ) -> None:
        if self._registrations.get(subscription_id) is registration:
            self._paused.add(subscription_id)
            self._errors[subscription_id] = error
            self._pause_shared(subscription_id)

    def _pause_shared(self, subscription_id: str) -> None:
        lease = self._delivery_leases.get(subscription_id)
        if self._coordinator is not None and lease is not None:
            self._coordinator.pause(subscription_id, lease)

    def _coordinator_forget(self, subscription_id: str) -> None:
        lease = self._delivery_leases.pop(subscription_id, None)
        if self._coordinator is not None and lease is not None:
            self._coordinator.forget(lease)

    async def _wait_for_work(self, registration: _Registration) -> None:
        if self._coordinator is None:
            await registration.wake.wait()
            return
        sleeper = asyncio.create_task(
            self._clock.sleep(self._coordinator.config.check_seconds)
        )
        notifier = asyncio.create_task(registration.wake.wait())
        try:
            await asyncio.wait((sleeper, notifier), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, notifier):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, notifier, return_exceptions=True)

    async def _finish_close(self, caller: asyncio.Task[object] | None) -> None:
        async with self._accept_lock:
            pass
        tasks = [task for task in self.delivery_tasks if task is not caller]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._registrations.clear()

    async def close(self, *, caller: asyncio.Task[object] | None = None) -> None:
        """Stop deliveries without discarding unacknowledged records."""
        if self._close_task is None:
            self.stop()
            self._close_task = asyncio.create_task(
                self._finish_close(
                    caller if caller is not None else asyncio.current_task()
                )
            )
        await asyncio.shield(self._close_task)
