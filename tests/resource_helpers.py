import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from aiohttp import web
from aiohttp.test_utils import TestServer

from dankmemer._parsing import Record
from dankmemer.http._routes import Route


@dataclass(frozen=True)
class Call:
    route: Route
    user_id: int | None
    cursor: int | None
    limit: int | None
    category: str | None
    automatic: bool


class Responses:
    def __init__(self, *payloads: object) -> None:
        self.payloads = list(payloads)
        self.calls: list[Call] = []

    async def __call__(
        self,
        route: Route,
        *,
        automatic: bool = False,
        user_id: int | None = None,
        cursor: int | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> object:
        self.calls.append(Call(route, user_id, cursor, limit, category, automatic))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


class ManualClock:
    def __init__(self) -> None:
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed

    def time(self) -> float:
        return 1_700_000_000.0 + self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.elapsed += seconds
        await asyncio.sleep(0)

    async def sleep_until(self, deadline: float) -> None:
        await self.sleep(max(0.0, deadline - self.monotonic()))


def blog(record_id: int) -> dict[str, object]:
    return {
        "id": str(record_id),
        "title": f"Blog {record_id}",
        "description": "A published summary",
        "createdAt": "2026-10-01T01:00:00.125Z",
        "url": f"https://dankmemer.lol/blog/{record_id}",
    }


def blogs(*ids: int, next_cursor: object = None) -> dict[str, object]:
    return {"data": [blog(record_id) for record_id in ids], "nextCursor": next_cursor}


def skin(reference: int | str | None = 12) -> dict[str, object]:
    return {
        "id": "test-skin",
        "key": "test",
        "name": "Test skin",
        "type": "item",
        "reference": reference,
        "rarity": "future-rarity",
        "imageUrl": "https://example.test/skin.png",
        "metadata": {"theme": {"colours": ["blue", "green"]}},
    }


def item(
    record_id: int = 12, name: str = "Test item", *, key: str = "test-item"
) -> dict[str, object]:
    return {
        "id": record_id,
        "key": key,
        "name": name,
        "type": "collectable",
        "value": 100,
        "sellValue": 20,
        "marketValue": 120,
        "flavor": "Flavour",
        "details": "Details",
        "imageUrl": "https://example.test/item.png",
        "tags": ["test"],
        "skins": [skin(record_id)],
        "futureField": {"ignored": True},
    }


def page(
    *records: Mapping[str, object],
    next_cursor: int | None = None,
    category: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "data": [dict(record) for record in records],
        "nextCursor": next_cursor,
    }
    if category is not None:
        payload["category"] = category
    return payload


FIXTURES = Path(__file__).parent / "fixtures" / "official_api_examples.json"


def example(route: str) -> object:
    body: object = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return (
        Record(body, path="official examples", field="")
        .record("responses")
        .value(route)
    )


def first_record(route: str) -> dict[str, object]:
    value = Record(example(route), path=route, field="").array("data")[0]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


@asynccontextmanager
async def local_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> AsyncGenerator[str, None]:
    app = web.Application()
    app.router.add_route("*", "/api/v1/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/api/v1"))
    finally:
        await server.close()
