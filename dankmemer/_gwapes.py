"""Internal response adapter for the limited Gwapes items API."""

from __future__ import annotations

from numbers import Real
from typing import Any


GWAPES_BASE_URL = "https://api.gwapes.com"

_RARITIES = {
    "Abundant",
    "Common",
    "Uncommon",
    "Rare",
    "Epic",
    "Legendary",
    "Mythical",
    "Godly",
}


def _is_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _optional_string(item: dict[str, Any], field: str) -> str | None:
    value = item.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Gwapes item field {field!r} must be a string or null")
    return value


def _optional_number(item: dict[str, Any], field: str) -> int | float | None:
    value = item.get(field)
    if value is not None and not _is_number(value):
        raise ValueError(f"Gwapes item field {field!r} must be numeric or null")
    return value


def _split_type(combined_type: str | None) -> tuple[str | None, str | None]:
    if combined_type is None:
        return None, None

    rarity, separator, item_type = combined_type.partition(" ")
    if separator and rarity in _RARITIES and item_type:
        return rarity, item_type
    return None, combined_type


def adapt_gwapes_items(payload: Any) -> dict[str, dict[str, Any]]:
    """Convert a Gwapes response into the legacy mapping expected by ItemsRoute."""

    if not isinstance(payload, dict):
        raise ValueError("Gwapes response must be a JSON object")
    if not isinstance(payload.get("message"), str):
        raise ValueError("Gwapes response must include a string message")
    if payload.get("success") is not True:
        message = payload.get("message")
        raise ValueError(f"Gwapes request was unsuccessful: {message}")

    body = payload.get("body")
    if not isinstance(body, list):
        raise ValueError("Gwapes response body must be a list")

    adapted: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(body):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Gwapes item at index {index} must be an object")

        name = raw_item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Gwapes item at index {index} has an invalid name")

        market_value = raw_item.get("value")
        if not _is_number(market_value):
            raise ValueError(f"Gwapes item at index {index} has an invalid value")

        attachment = _optional_string(raw_item, "attachment")
        net_value = _optional_number(raw_item, "net_value")
        combined_type = _optional_string(raw_item, "type")
        rarity, item_type = _split_type(combined_type)

        adapted[str(index)] = {
            "id": None,
            "name": name,
            "details": None,
            "emoji": None,
            "flavor": None,
            "hasUse": None,
            "imageURL": attachment,
            "itemKey": None,
            "marketValue": market_value,
            "netValue": net_value,
            "rarity": rarity,
            "skins": None,
            "tags": None,
            "type": item_type,
            "value": None,
        }

    return adapted
