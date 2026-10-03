import asyncio
import os
import sys
from pathlib import Path

from dankmemer import DankMemer, EventConfig, EventDelivery, LotteryResult
from dankmemer.http._routes import LOTTERY, Route
from dankmemer.storage.sqlite import SqliteEventStore


class OfflineClient(DankMemer):
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
        return {
            "data": {
                "drawnAt": "2026-10-01T01:00:00Z",
                "winnings": 100,
                "totalEntries": 10,
                "participants": 3,
                "winnerEntries": 1,
            }
        }


async def run(path: Path, marker: Path) -> None:
    async with await SqliteEventStore.open(path) as store:
        client = OfflineClient(
            "test-token",
            event_store=store,
            events=EventConfig(delivery=EventDelivery.DURABLE, emit_initial=True),
        )

        @client.event(subscription_id="crash.lottery")
        async def on_lottery_result(result: LotteryResult) -> None:
            marker.write_text(str(result.winnings), encoding="utf-8")
            # Exit before the worker can acknowledge the successful callback.
            os._exit(0)

        async with client:
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2])))
