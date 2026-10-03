import asyncio
from datetime import datetime

import pytest
from resource_helpers import Responses

from dankmemer import (
    DankMemer,
    EventConfig,
    EventDelivery,
    EventStore,
    PollingConfig,
    PollingResource,
)
from dankmemer.http._admission import Admission


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_700_000_000.0 + self.now

    async def sleep(self, seconds: float) -> None:
        await self.sleep_until(self.now + seconds)

    async def sleep_until(self, deadline: float) -> None:
        if deadline <= self.now:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiters.append((deadline, future))
        await future

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in self.waiters:
            if deadline <= self.now and not future.done():
                future.set_result(None)
        self.waiters = [
            (at, future) for at, future in self.waiters if not future.done()
        ]


class CalendarClock(ManualClock):
    def __init__(self, wall_time: datetime) -> None:
        super().__init__()
        self.epoch = wall_time.timestamp()

    def time(self) -> float:
        return self.epoch + self.now


def offline_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: Responses,
    *,
    clock: ManualClock | None = None,
    events: EventConfig | None = None,
    polling: PollingConfig | None = None,
    event_store: EventStore | None = None,
) -> DankMemer:
    selected_clock = clock if clock is not None else ManualClock()
    monkeypatch.setattr("dankmemer.client.Admission", lambda: Admission(selected_clock))
    monkeypatch.setattr(DankMemer, "_request_json", responses)
    return DankMemer(
        "test-token", events=events, polling=polling, event_store=event_store
    )


async def settle() -> None:
    for _ in range(40):
        await asyncio.sleep(0)


def lottery(hour: int) -> dict[str, object]:
    return {
        "data": {
            "drawnAt": f"2026-10-01T{hour:02}:00:00Z",
            "winnings": 100,
            "totalEntries": 10,
            "participants": 3,
            "winnerEntries": 1,
        }
    }


def boosts(*multipliers: float) -> dict[str, object]:
    return {
        "data": [
            {"type": "coins", "multiplier": value, "endsAt": "2026-10-01T12:00:00Z"}
            for value in multipliers
        ]
    }


def durable(
    *, emit_initial: bool = False, maximum: int = 1_000, page_size: int = 100
) -> EventConfig:
    return EventConfig(
        delivery=EventDelivery.DURABLE,
        emit_initial=emit_initial,
        max_pending_deliveries=maximum,
        publication_page_size=page_size,
    )


async def wait_checkpoint(
    store: EventStore, resource: PollingResource, version: int
) -> None:
    async with asyncio.timeout(3):
        while True:
            checkpoint = await store.read_checkpoint(resource)
            if checkpoint is not None and checkpoint.version >= version:
                return
            await asyncio.sleep(0.001)


async def wait_count(
    store: EventStore, expected: int, subscription_id: str | None = None
) -> None:
    async with asyncio.timeout(3):
        while await store.count_pending_callbacks(subscription_id) != expected:
            await asyncio.sleep(0.001)


async def wait_paused(client: DankMemer, subscription_id: str) -> None:
    async with asyncio.timeout(3):
        while subscription_id not in client.paused_event_subscriptions:
            await asyncio.sleep(0.001)
