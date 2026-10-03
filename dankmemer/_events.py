from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from ._clock import Clock as _Clock
from ._clock import SystemClock as _SystemClock
from .enums import PollingResource
from .errors import ConfigurationError, DankMemerHTTPError, LifecycleError

__all__ = ("_DemandScheduler",)

_Listener = Callable[..., Awaitable[None]]
_Poll = Callable[[PollingResource, frozenset[str]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Registration:
    event: str
    callback: _Listener


def same_callback(first: _Listener, second: _Listener) -> bool:
    if first is second:
        return True
    first_function = getattr(first, "__func__", None)
    return (
        first_function is not None
        and first_function is getattr(second, "__func__", None)
        and getattr(first, "__self__", None) is getattr(second, "__self__", None)
    )


def is_async_callback(callback: object) -> bool:
    return callable(callback) and (
        inspect.iscoroutinefunction(callback)
        or inspect.iscoroutinefunction(type(callback).__call__)
    )


def _validate_dependency(event: object, resource: object) -> None:
    if not isinstance(event, str) or not event:
        raise ConfigurationError("event names must be nonempty strings")
    if resource is not None and not isinstance(resource, PollingResource):
        raise ConfigurationError("event dependencies must use polling resources")


def _validate_interval(
    resource: object, seconds: object, *, minimum: float = 60.0
) -> None:
    if not isinstance(resource, PollingResource):
        raise ConfigurationError("intervals must use polling resources")
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < minimum
    ):
        raise ConfigurationError(
            f"scheduler interval must be at least {minimum:g} seconds"
        )


def _checked_minimum(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigurationError("minimum_interval must be positive and finite")
    return float(value)


class _DemandScheduler:
    """Poll resources required by registered listeners.

    Each resource has one polling worker, which receives only the event names
    registered for that resource. Callback delivery is handled by the observer.
    """

    def __init__(
        self,
        dependencies: Mapping[str, PollingResource | None],
        intervals: Mapping[PollingResource, float],
        poll: _Poll,
        *,
        clock: _Clock | None = None,
        logger: logging.Logger | None = None,
        next_delay: Callable[[PollingResource], float] | None = None,
        initial_delay: Callable[[], float] | None = None,
        minimum_interval: float = 60.0,
        expected_cancel: Callable[[PollingResource], bool] | None = None,
    ) -> None:
        minimum_interval = _checked_minimum(minimum_interval)
        for event, resource in dependencies.items():
            _validate_dependency(event, resource)
        for resource, seconds in intervals.items():
            _validate_interval(resource, seconds, minimum=minimum_interval)
        if not inspect.iscoroutinefunction(poll):
            raise ConfigurationError("poll must be an async function")

        self._dependencies = dict(dependencies)
        self._intervals = dict(intervals)
        self._poll = poll
        self._clock = clock if clock is not None else _SystemClock()
        self._logger = (
            logger if logger is not None else logging.getLogger("dankmemer.events")
        )
        self._registrations: dict[int, _Registration] = {}
        self._external_resources: frozenset[PollingResource] = frozenset()
        self._minimum_interval = minimum_interval
        self._expected_cancel = expected_cancel
        self._next_registration = 0
        self._tasks: dict[PollingResource, asyncio.Task[None]] = {}
        self._paused: set[PollingResource] = set()
        self._next_delay = next_delay
        self._initial_delay = initial_delay
        self._changes = {resource: asyncio.Event() for resource in intervals}
        self._next_deadline: dict[PollingResource, float] = {}
        self._running = False
        self._closed = False

    @property
    def active_resources(self) -> frozenset[PollingResource]:
        """Resources currently required by registered listeners."""
        return self._external_resources | frozenset(
            resource
            for registration in self._registrations.values()
            if (resource := self._dependencies[registration.event]) in self._intervals
        )

    @property
    def paused_resources(self) -> frozenset[PollingResource]:
        """Resources awaiting an explicit retry after HTTP 400, 401, or 403."""
        return frozenset(self._paused)

    def retry_polling(self, resource: object) -> None:
        """Resume a paused resource without bypassing request spacing."""
        if self._closed or not self._running:
            raise LifecycleError("event scheduler is not running")
        if not isinstance(resource, PollingResource) or resource not in self._intervals:
            raise ConfigurationError("resource must have automatic polling enabled")
        if resource in self._paused:
            self._paused.remove(resource)
            self._ensure_worker(resource)

    def _events_for(self, resource: PollingResource) -> frozenset[str]:
        if resource in self._external_resources:
            return frozenset(
                event
                for event, dependency in self._dependencies.items()
                if dependency is resource
            )
        return frozenset(
            registration.event
            for registration in self._registrations.values()
            if self._dependencies[registration.event] is resource
        )

    def set_external_resources(self, resources: frozenset[PollingResource]) -> None:
        """Include subscriptions registered by other shared clients."""
        previous = self._external_resources
        self._external_resources = frozenset(
            resource for resource in resources if resource in self._intervals
        )
        if self._running:
            for resource in self._external_resources:
                self._ensure_worker(resource)
            for resource in previous - self._external_resources:
                self._changes[resource].set()

    def add_listener(self, event: str, callback: _Listener) -> int:
        """Register an async callback and return its registration ID.

        Registering the same callback for the same event again returns the
        existing ID. A running scheduler starts polling newly needed resources.
        """
        if self._closed:
            raise LifecycleError("event scheduler is closed")
        if event not in self._dependencies:
            raise ConfigurationError("unknown event name")
        if not is_async_callback(callback):
            raise ConfigurationError("event listener must be an async callable")
        for registration_id, registration in self._registrations.items():
            if registration.event == event and same_callback(
                registration.callback, callback
            ):
                return registration_id

        self._next_registration += 1
        registration_id = self._next_registration
        self._registrations[registration_id] = _Registration(event, callback)
        resource = self._dependencies[event]
        if self._running and resource is not None:
            self._ensure_worker(resource)
        return registration_id

    def remove_listener(self, registration_id: int) -> bool:
        """Remove a polling registration; return false for an unknown ID."""
        registration = self._registrations.pop(registration_id, None)
        if registration is None:
            return False
        resource = self._dependencies[registration.event]
        if resource in self._changes:
            self._changes[resource].set()
        return True

    async def start(self) -> None:
        """Start poll workers for resources with active listeners."""
        if self._closed:
            raise LifecycleError("event scheduler is closed")
        if self._running:
            return
        self._running = True
        for resource in self.active_resources:
            self._ensure_worker(resource)

    async def close(self, *, caller: asyncio.Task[object] | None = None) -> None:
        """Cancel polling workers and clear their registrations."""
        if self._closed:
            return
        self._closed = True
        self._running = False
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._tasks.values()
            if task is not current and task is not caller
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._registrations.clear()

    def _ensure_worker(self, resource: PollingResource) -> None:
        if (
            resource not in self._intervals
            or resource in self._paused
            or not self._events_for(resource)
        ):
            return
        task = self._tasks.get(resource)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._run_resource(resource))
        self._tasks[resource] = task
        task.add_done_callback(lambda finished: self._worker_done(resource, finished))

    def _worker_done(self, resource: PollingResource, task: asyncio.Task[None]) -> None:
        if self._tasks.get(resource) is task:
            del self._tasks[resource]
        failed = task.cancelled() or task.exception() is not None
        if failed and self._running:
            expected = (
                task.cancelled()
                and self._expected_cancel is not None
                and self._expected_cancel(resource)
            )
            if not expected:
                self._logger.error(
                    "event poll worker stopped unexpectedly for %s", resource
                )
            # A crashed worker must not restart with an immediate extra poll.
            self._next_deadline[resource] = max(
                self._next_deadline.get(resource, float("-inf")),
                self._clock.monotonic() + self._intervals[resource],
            )
        if self._running:
            self._ensure_worker(resource)

    async def _wait_until(self, resource: PollingResource, deadline: float) -> None:
        changed = self._changes[resource]
        if changed.is_set():
            changed.clear()
            return
        if deadline <= self._clock.monotonic():
            return
        sleeper = asyncio.create_task(self._clock.sleep_until(deadline))
        notifier = asyncio.create_task(changed.wait())
        try:
            done, _ = await asyncio.wait(
                (sleeper, notifier), return_when=asyncio.FIRST_COMPLETED
            )
            if notifier in done:
                changed.clear()
            if sleeper in done:
                sleeper.result()
        finally:
            for task in (sleeper, notifier):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, notifier, return_exceptions=True)

    async def _run_resource(self, resource: PollingResource) -> None:
        interval = self._intervals[resource]
        deadline = self._next_deadline.get(resource)
        if deadline is None:
            delay = 0.0 if self._initial_delay is None else self._initial_delay()
            deadline = self._clock.monotonic() + delay
            self._next_deadline[resource] = deadline
        while self._running:
            events = self._events_for(resource)
            if not events:
                return
            if self._clock.monotonic() < deadline:
                await self._wait_until(resource, deadline)
                continue

            try:
                await self._poll(resource, events)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._logger.error(
                    "event poll failed for %s (%s)", resource, type(error).__name__
                )
                if isinstance(error, DankMemerHTTPError) and error.status_code in (
                    400,
                    401,
                    403,
                ):
                    # These responses require fixing the cause before retrying.
                    self._paused.add(resource)
                    self._next_deadline[resource] = max(
                        deadline, self._clock.monotonic() + 60.0
                    )
                    return

            now = self._clock.monotonic()
            if self._next_delay is None:
                # Skip missed slots after a slow poll instead of sending a burst.
                missed = max(1, math.floor((now - deadline) / interval) + 1)
                deadline += missed * interval
            else:
                delay = self._next_delay(resource)
                _validate_interval(resource, delay, minimum=self._minimum_interval)
                deadline = now + delay
            self._next_deadline[resource] = deadline
