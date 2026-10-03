# pyright: reportPrivateUsage=false

import asyncio
import os
import sys
from pathlib import Path

from dankmemer import (
    CoordinatedEventStore,
    CoordinationConfig,
    CoordinationMode,
    DankMemer,
    EventConfig,
    EventDelivery,
    LotteryResult,
)
from dankmemer.http._routes import LOTTERY, Route
from dankmemer.storage.postgres import PostgresEventStore
from dankmemer.storage.sqlite import SqliteEventStore


class OfflineClient(DankMemer):
    directory: Path
    worker_name: str

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
        assert route is LOTTERY and automatic
        await self._admission.acquire(
            path=route.template,
            resource=route.event_resource,
            automatic=True,
            deadline=None,
        )
        with (self.directory / (self.worker_name + ".requests")).open("a") as requests:
            requests.write("lottery\n")
        await self._admission.observe({})
        return {
            "data": {
                "drawnAt": "2026-10-01T01:00:00Z",
                "winnings": 100,
                "totalEntries": 10,
                "participants": 3,
                "winnerEntries": 1,
            }
        }


async def run(
    backend: str,
    location: str,
    schema: str,
    directory: Path,
    name: str,
    mode: str,
) -> None:
    store: CoordinatedEventStore
    if backend == "sqlite":
        store = await SqliteEventStore.open(location)
    else:
        store = await PostgresEventStore.open(location, schema=schema)
    try:
        client = OfflineClient(
            "test-token",
            event_store=store,
            events=EventConfig(delivery=EventDelivery.DURABLE, emit_initial=True),
            coordination=CoordinationConfig(
                mode=CoordinationMode.SHARED,
                application_id="test-application",
                lease_seconds=2.0,
                heartbeat_seconds=0.2,
                check_seconds=0.05,
            ),
            silent=True,
        )
        client.directory = directory
        client.worker_name = name

        @client.event(
            subscription_id="consumer.shared"
            if mode == "compete"
            else "consumer." + name
        )
        async def on_lottery_result(result: LotteryResult) -> None:
            (directory / (name + ".callback")).write_text(str(result.winnings))
            if mode == "crash":
                # Leave the callback unacknowledged and the ownership lease intact.
                os._exit(0)

        await client._events.prepare()
        (directory / (name + ".ready")).touch()
        while not (directory / "go").exists():
            await asyncio.sleep(0.01)
        async with client:
            while not (directory / "stop").exists():
                await asyncio.sleep(0.01)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(
        run(
            sys.argv[1],
            sys.argv[2],
            sys.argv[3],
            Path(sys.argv[4]),
            sys.argv[5],
            sys.argv[6],
        )
    )
