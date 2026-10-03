from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from dankmemer._parsing import Record
from dankmemer.enums import PollingResource
from dankmemer.errors import DankMemerTimeoutError, EventPayloadError, RateLimited


@dataclass(slots=True)
class _Window:
    limit: int
    remaining: int
    resets_at: float


def integer_header(value: str | None, *, minimum: int) -> int | None:
    if value is None or len(value) > 13 or not value.isascii() or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if parsed >= minimum else None


class RequestBudget:
    def __init__(self, epoch: float) -> None:
        self._minute = _Window(60, 60, (math.floor(epoch / 60) + 1) * 60)
        self._day = _Window(10_000, 10_000, (math.floor(epoch / 86_400) + 1) * 86_400)
        self._last_attempt = float("-inf")
        self._cooldown_until = float("-inf")
        self._resource_last: dict[PollingResource, float] = {}

    def _refresh(self, epoch: float) -> None:
        for window, period in ((self._minute, 60), (self._day, 86_400)):
            if epoch >= window.resets_at:
                window.remaining = window.limit
                window.resets_at = (math.floor(epoch / period) + 1) * period

    def reserve(
        self,
        now: float,
        epoch: float,
        *,
        path: str,
        resource: PollingResource | None,
        automatic: bool,
        deadline: float | None,
    ) -> float:
        self._refresh(epoch)
        if self._day.remaining == 0:
            retry_after = max(0.0, self._day.resets_at - epoch)
            raise RateLimited(
                "daily request allowance is exhausted",
                status_code=429,
                path=path,
                retry_after_seconds=retry_after,
            )

        earliest = max(
            now,
            self._last_attempt + 60.0 / self._minute.limit,
            self._cooldown_until,
        )
        if self._minute.remaining == 0:
            earliest = max(earliest, now + max(0.0, self._minute.resets_at - epoch))
        if automatic and resource is not None:
            last = self._resource_last.get(resource)
            if last is not None:
                earliest = max(earliest, last + 60.0)

        if deadline is not None and earliest >= deadline:
            raise DankMemerTimeoutError(
                "request deadline expired while waiting for admission",
                path=path,
            )
        if earliest <= now:
            # A failed network attempt still consumes its reserved slot.
            self._minute.remaining -= 1
            self._day.remaining -= 1
            self._last_attempt = now
            if resource is not None:
                # Manual requests also postpone the next automatic poll.
                self._resource_last[resource] = now
            return 0.0
        return earliest - now

    def update_headers(self, epoch: float, headers: Mapping[str, str]) -> None:
        self._refresh(epoch)
        for window, suffix in ((self._minute, "Minute"), (self._day, "Day")):
            limit = integer_header(
                headers.get(f"X-RateLimit-Limit-{suffix}"), minimum=1
            )
            remaining = integer_header(
                headers.get(f"X-RateLimit-Remaining-{suffix}"), minimum=0
            )
            reset = integer_header(
                headers.get(f"X-RateLimit-Reset-{suffix}"), minimum=0
            )
            if limit is not None:
                if suffix == "Minute":
                    limit = min(limit, 10_000)
                if limit <= window.limit or remaining is not None:
                    # Keep slots reserved by requests still in flight.
                    window.remaining = min(
                        limit,
                        max(0, window.remaining + limit - window.limit),
                    )
                    window.limit = limit
            if remaining is not None:
                window.remaining = min(window.remaining, remaining)
            if reset is not None and reset >= epoch:
                window.resets_at = max(window.resets_at, float(reset))

    def delay(self, until: float) -> None:
        self._cooldown_until = max(self._cooldown_until, until)

    def dump(self) -> dict[str, object]:
        return {
            "minute": {
                "limit": self._minute.limit,
                "remaining": self._minute.remaining,
                "reset": self._minute.resets_at,
            },
            "day": {
                "limit": self._day.limit,
                "remaining": self._day.remaining,
                "reset": self._day.resets_at,
            },
            "last": None
            if not math.isfinite(self._last_attempt)
            else self._last_attempt,
            "cooldown": None
            if not math.isfinite(self._cooldown_until)
            else self._cooldown_until,
            "resources": {
                resource.value: {"last": value}
                for resource, value in self._resource_last.items()
            },
        }

    @classmethod
    def load(cls, record: Record) -> Self:
        budget = cls(0)
        for suffix, window in (("minute", budget._minute), ("day", budget._day)):
            entry = record.record(suffix)
            window.limit = entry.integer("limit")
            window.remaining = entry.integer("remaining")
            window.resets_at = entry.number("reset")
            if window.limit < 1 or not 0 <= window.remaining <= window.limit:
                raise EventPayloadError("invalid shared request budget")
        budget._last_attempt = (
            float("-inf") if record.value("last") is None else record.number("last")
        )
        budget._cooldown_until = (
            float("-inf")
            if record.value("cooldown") is None
            else record.number("cooldown")
        )
        budget._resource_last = {
            PollingResource(key): value
            for key, value in record.mapping(
                "resources", lambda entry: entry.number("last")
            ).items()
        }
        return budget
