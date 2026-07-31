import warnings

import pytest

from dankmemer import Above, DankMemerClient, ItemsFilter
from dankmemer._gwapes import adapt_gwapes_items
from dankmemer.exceptions import (
    DankMemerResponseException,
    UnsupportedRouteException,
)


class DummyResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class DummySession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.closed = False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


def gwapes_payload():
    return {
        "message": "Success",
        "success": True,
        "body": [
            {
                "name": "Ammo",
                "value": 5_249_000,
                "attachment": "https://cdn.example/ammo.png",
                "lastUpdate": "2026-07-31T00:24:49.198Z",
                "net_value": 100_000,
                "type": "Rare Buff",
            },
            {
                "name": "Mystery Item",
                "value": 50,
                "attachment": None,
                "lastUpdate": "2026-07-31T00:24:49.198Z",
                "net_value": None,
                "type": None,
            },
            {
                "name": "Beaker of sus fluid",
                "value": 4_662_000,
                "attachment": "https://cdn.example/beaker-old.png",
                "lastUpdate": "2026-04-05T12:39:56.385Z",
                "net_value": 200_000,
                "type": "Seasonal",
            },
            {
                "name": "Beaker of Sus Fluid",
                "value": 4_662_000,
                "attachment": "https://cdn.example/beaker-new.png",
                "lastUpdate": "2026-07-29T06:34:38.476Z",
                "net_value": None,
                "type": None,
            },
        ],
    }


def test_adapter_maps_supported_fields_and_preserves_duplicate_names():
    adapted = adapt_gwapes_items(gwapes_payload())

    assert list(adapted) == ["0", "1", "2", "3"]
    ammo = adapted["0"]
    assert ammo["name"] == "Ammo"
    assert ammo["imageURL"] == "https://cdn.example/ammo.png"
    assert ammo["marketValue"] == 5_249_000
    assert ammo["netValue"] == 100_000
    assert ammo["rarity"] == "Rare"
    assert ammo["type"] == "Buff"
    assert ammo["id"] is None
    assert ammo["itemKey"] is None
    assert ammo["value"] is None

    assert adapted["1"]["imageURL"] is None
    assert adapted["1"]["netValue"] is None
    assert adapted["1"]["rarity"] is None
    assert adapted["1"]["type"] is None
    assert adapted["2"]["rarity"] is None
    assert adapted["2"]["type"] == "Seasonal"
    assert adapted["2"]["name"].casefold() == adapted["3"]["name"].casefold()


@pytest.mark.asyncio
async def test_default_items_query_uses_gwapes_without_rule_warning():
    session = DummySession([DummyResponse(gwapes_payload())])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        logging_mode="null",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        items = await client.items.query()

    assert caught == []
    assert session.requests[0][0] == "https://api.gwapes.com/items"
    assert [item.name for item in items] == [
        "Ammo",
        "Mystery Item",
        "Beaker of sus fluid",
        "Beaker of Sus Fluid",
    ]
    assert items[0].rarity == "Rare"
    assert items[0].type == "Buff"
    assert items[0].details is None
    assert items[0].hasUse is None
    assert items[0].skins is None
    assert items[0].tags is None
    assert items[0].value is None


@pytest.mark.asyncio
async def test_supported_and_unavailable_item_filters_are_safe():
    session = DummySession([DummyResponse(gwapes_payload())])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        logging_mode="null",
    )

    assert [item.name for item in await client.items.query(ItemsFilter(name="Ammo"))] == [
        "Ammo"
    ]
    assert [
        item.name for item in await client.items.query(ItemsFilter(rarity="Rare"))
    ] == ["Ammo"]
    assert [
        item.name for item in await client.items.query(ItemsFilter(type="Buff"))
    ] == ["Ammo"]
    assert len(await client.items.query(ItemsFilter(marketValue=Above(1_000_000)))) == 3
    assert len(await client.items.query(ItemsFilter(netValue=Above(150_000)))) == 1

    assert await client.items.query(ItemsFilter(id=1)) == []
    assert await client.items.query(ItemsFilter(details="missing")) == []
    assert await client.items.query(ItemsFilter(hasUse=True)) == []
    assert await client.items.query(ItemsFilter(skins={})) == []
    assert await client.items.query(ItemsFilter(value=1)) == []
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_default_non_item_routes_raise_before_network_request():
    session = DummySession([])
    client = DankMemerClient(session=session, logging_mode="null")

    for route in client._routes:
        if route == "items":
            continue
        with pytest.raises(UnsupportedRouteException) as exc_info:
            await client.request(route)
        assert exc_info.value.route == route
        assert exc_info.value.supported_routes == ("items",)
        assert exc_info.value.rule_url == "https://dankmemer.lol/rules"
        assert "limited-field Gwapes API" in str(exc_info.value)
        assert "Rule 13" in str(exc_info.value)

    assert session.requests == []


@pytest.mark.asyncio
async def test_custom_base_url_retains_legacy_response_behavior():
    payload = {
        "trending_game": {
            "name": "Among Us",
            "lastModified": "2026-07-31T00:24:49.198Z",
        }
    }
    session = DummySession([DummyResponse(payload), DummyResponse(payload)])
    client = DankMemerClient(
        base_url="https://example.test/dank/",
        session=session,
        useAntirateLimit=False,
        logging_mode="null",
    )

    result = await client.request("stream")
    stream = await client.stream.query()

    assert result == payload
    assert stream.trending_game.name == "Among Us"
    assert session.requests[0][0] == "https://example.test/dank/stream"
    assert session.requests[1][0] == "https://example.test/dank/stream"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"success": True, "body": []},
        {"message": "Unavailable", "success": False, "body": []},
        {"message": "Success", "success": True, "body": {}},
        {
            "message": "Success",
            "success": True,
            "body": [{"name": "", "value": 1}],
        },
        {
            "message": "Success",
            "success": True,
            "body": [{"name": "Trash", "value": "unknown"}],
        },
    ],
)
async def test_malformed_gwapes_responses_are_wrapped(payload):
    session = DummySession([DummyResponse(payload)])
    client = DankMemerClient(
        session=session,
        useAntirateLimit=False,
        logging_mode="null",
    )

    with pytest.raises(DankMemerResponseException) as exc_info:
        await client.request("items")

    assert exc_info.value.status_code == 200
    assert exc_info.value.route == "items"
    assert "Invalid Gwapes response" in str(exc_info.value)
