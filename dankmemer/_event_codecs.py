from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Generic, TypeVar, cast

from ._event_sources import PublicationState
from ._live_events import TrendingGameState
from ._parsing import Record
from .enums import PollingResource
from .errors import DankMemerResponseError, EventPayloadError
from .models.activities import (
    GlobalBoost,
    LotteryResult,
    MerchantRotation,
    parse_global_boost,
    parse_lottery,
    parse_merchant_rotation,
)
from .models.live import (
    Drop,
    FishingEvent,
    StoreDailyGift,
    StoreSale,
    parse_drop,
    parse_fishing_event,
    parse_live_collection,
    parse_store_daily_gift,
    parse_store_sale,
)
from .models.publications import Blog, Changelog, parse_blog, parse_changelog
from .types import JSONValue

_T = TypeVar("_T")
_P = TypeVar("_P", Blog, Changelog)
_R = TypeVar("_R", Drop, StoreSale, StoreDailyGift, FishingEvent)

# Payload versions are independent of the store's table schema version.
_FORMAT = "dankmemer.py/events"
_VERSION = 1


class JSONEventCodec(Generic[_T]):
    """Validate versioned snapshots and callback arguments without loading code."""

    def __init__(
        self,
        resource: PollingResource,
        encode: Callable[[_T], object],
        decode: Callable[[Record], _T],
        encode_arg: Callable[[object], object],
        decode_arg: Callable[[Record], object],
    ) -> None:
        self._resource = resource
        self._encode = encode
        self._decode = decode
        self._encode_arg = encode_arg
        self._decode_arg = decode_arg

    def _dump(self, kind: str, value: object) -> bytes:
        return json.dumps(
            {
                "format": _FORMAT,
                "version": _VERSION,
                "resource": self._resource.value,
                "kind": kind,
                "value": value,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _load(self, payload: bytes, kind: str) -> Record:
        try:
            value: object = json.loads(payload)
            record = Record(value, path=f"event_store/{self._resource.value}", field="")
            if (
                record.string("format") != _FORMAT
                or record.integer("version") != _VERSION
            ):
                raise EventPayloadError(
                    "unsupported saved event payload format or version"
                )
            if (
                record.string("resource") != self._resource.value
                or record.string("kind") != kind
            ):
                raise EventPayloadError(
                    "saved event payload does not match its resource or purpose"
                )
            return record
        except (ValueError, UnicodeError, DankMemerResponseError) as error:
            raise EventPayloadError("invalid saved event JSON or fields") from error

    def encode_snapshot(self, value: _T) -> bytes:
        return self._dump("snapshot", self._encode(value))

    def decode_snapshot(self, payload: bytes) -> _T:
        try:
            return self._decode(self._load(payload, "snapshot").record("value"))
        except DankMemerResponseError as error:
            raise EventPayloadError("invalid saved event snapshot") from error

    def encode_args(self, args: tuple[object, ...]) -> bytes:
        return self._dump("args", [self._encode_arg(value) for value in args])

    def decode_args(self, payload: bytes) -> tuple[object, ...]:
        try:
            return tuple(
                self._decode_arg(record)
                for record in self._load(payload, "args").records("value")
            )
        except DankMemerResponseError as error:
            raise EventPayloadError("invalid saved callback arguments") from error


def _boosts(value: tuple[GlobalBoost, ...]) -> dict[str, object]:
    return {
        "data": [
            {
                "type": boost.type,
                "multiplier": boost.multiplier,
                "endsAt": boost.ends_at.isoformat(),
            }
            for boost in value
        ]
    }


def _read_boosts(record: Record) -> tuple[GlobalBoost, ...]:
    return tuple(parse_global_boost(entry) for entry in record.records("data"))


def _boost_arg(value: object) -> dict[str, object]:
    if not isinstance(value, tuple) or not all(
        isinstance(entry, GlobalBoost) for entry in cast(tuple[object, ...], value)
    ):
        raise EventPayloadError("expected a tuple of boosts for callback arguments")
    return _boosts(cast(tuple[GlobalBoost, ...], value))


def boosts_codec() -> JSONEventCodec[tuple[GlobalBoost, ...]]:
    return JSONEventCodec(
        PollingResource.GLOBAL_BOOSTS, _boosts, _read_boosts, _boost_arg, _read_boosts
    )


def _lottery(value: LotteryResult | None) -> dict[str, object]:
    return {
        "data": None
        if value is None
        else {
            "drawnAt": value.drawn_at.isoformat(),
            "winnings": value.winnings,
            "totalEntries": value.total_entries,
            "participants": value.participants,
            "winnerEntries": value.winner_entries,
        }
    }


def _read_lottery(record: Record) -> LotteryResult | None:
    value = record.nullable_record("data")
    return None if value is None else parse_lottery(value)


def _lottery_arg(value: object) -> dict[str, object]:
    if not isinstance(value, LotteryResult):
        raise EventPayloadError("expected a lottery result for callback arguments")
    return _lottery(value)


def lottery_codec() -> JSONEventCodec[LotteryResult | None]:
    return JSONEventCodec(
        PollingResource.LOTTERY,
        _lottery,
        _read_lottery,
        _lottery_arg,
        lambda record: parse_lottery(record.record("data")),
    )


def _json(value: JSONValue) -> object:
    if isinstance(value, Mapping):
        return {key: _json(entry) for key, entry in value.items()}
    if isinstance(value, tuple):
        return [_json(entry) for entry in value]
    return value


def _merchant(value: MerchantRotation | None) -> dict[str, object]:
    return {
        "data": None
        if value is None
        else {
            "date": value.date.isoformat(),
            "generatedAt": value.generated_at.isoformat(),
            "trades": [
                {
                    "position": trade.position,
                    "max": trade.max,
                    "reward": _json(trade.reward.data),
                    "forReward": _json(trade.cost.data),
                }
                for trade in value.trades
            ],
        }
    }


def _read_merchant(record: Record) -> MerchantRotation | None:
    value = record.nullable_record("data")
    return None if value is None else parse_merchant_rotation(value)


def _merchant_arg(value: object) -> dict[str, object]:
    if not isinstance(value, MerchantRotation):
        raise EventPayloadError("expected a merchant rotation for callback arguments")
    return _merchant(value)


def merchant_codec() -> JSONEventCodec[MerchantRotation | None]:
    return JSONEventCodec(
        PollingResource.MERCHANT_TRADES,
        _merchant,
        _read_merchant,
        _merchant_arg,
        lambda record: parse_merchant_rotation(record.record("data")),
    )


def _publication(value: Blog | Changelog) -> dict[str, object]:
    result: dict[str, object] = {
        "id": value.id,
        "title": value.title,
        "createdAt": value.created_at.isoformat(),
        "url": value.url,
    }
    if isinstance(value, Blog):
        result["description"] = value.description
    return result


def _publication_state(value: PublicationState[_P]) -> dict[str, object]:
    return {
        "headId": value.head_id,
        "recentIds": list(value.recent_ids),
        "newEntries": [_publication(entry) for entry in value.new_entries],
    }


def _read_publication_state(
    record: Record, parse: Callable[[Record], _P]
) -> PublicationState[_P]:
    head_id = record.value("headId")
    if head_id is not None and not isinstance(head_id, str):
        raise EventPayloadError("saved publication head ID must be a string or null")
    recent_ids = record.strings("recentIds")
    if len(set(recent_ids)) != len(recent_ids) or (
        head_id is not None and head_id not in recent_ids
    ):
        raise EventPayloadError("saved publication checkpoint has inconsistent IDs")
    return PublicationState(
        head_id,
        recent_ids,
        tuple(parse(entry) for entry in record.records("newEntries")),
    )


def _blog_arg(value: object) -> dict[str, object]:
    if not isinstance(value, Blog):
        raise EventPayloadError("expected a blog for callback arguments")
    return _publication(value)


def _changelog_arg(value: object) -> dict[str, object]:
    if not isinstance(value, Changelog):
        raise EventPayloadError("expected a changelog for callback arguments")
    return _publication(value)


def blogs_codec() -> JSONEventCodec[PublicationState[Blog]]:
    return JSONEventCodec(
        PollingResource.BLOGS,
        _publication_state,
        lambda record: _read_publication_state(record, parse_blog),
        _blog_arg,
        parse_blog,
    )


def changelogs_codec() -> JSONEventCodec[PublicationState[Changelog]]:
    return JSONEventCodec(
        PollingResource.CHANGELOGS,
        _publication_state,
        lambda record: _read_publication_state(record, parse_changelog),
        _changelog_arg,
        parse_changelog,
    )


def _drop(value: Drop) -> dict[str, object]:
    return {
        "id": value.id,
        "kind": value.kind,
        "reward": None if value.reward is None else _json(value.reward.data),
        "cost": None if value.cost is None else _json(value.cost.data),
        "specialCost": value.special_cost,
        "startsAt": value.starts_at.isoformat(),
        "endsAt": value.ends_at.isoformat(),
        "totalStock": value.total_stock,
        "limitPerUser": value.limit_per_user,
        "patreonOnly": value.patreon_only,
        "partnerOnly": value.partner_only,
    }


def _sale(value: StoreSale) -> dict[str, object]:
    return {
        "id": value.id,
        "subjectID": value.subject_id,
        "subjectKind": value.subject_kind,
        "startsAt": value.starts_at.isoformat(),
        "endsAt": value.ends_at.isoformat(),
        "price": value.price,
        "name": value.name,
        "url": value.url,
    }


def _gift(value: StoreDailyGift) -> dict[str, object]:
    return {
        "id": value.id,
        "name": value.name,
        "category": value.category,
        "day": value.day.isoformat(),
        "expiresAt": value.expires_at.isoformat(),
        "reward": _json(value.reward.data),
        "metadata": _json(value.metadata.data),
    }


def _fishing_event(value: FishingEvent) -> dict[str, object]:
    return {
        "id": value.id,
        "typeId": value.type_id,
        "name": value.name,
        "description": value.description,
        "imageUrl": value.image_url,
        "premiumOnly": value.premium_only,
        "startsAt": value.starts_at.isoformat(),
        "endsAt": value.ends_at.isoformat(),
    }


def _live_codec(
    resource: PollingResource,
    model: type[_R],
    encode: Callable[[_R], dict[str, object]],
    parse: Callable[[Record], _R],
) -> JSONEventCodec[tuple[_R, ...]]:
    def snapshot(values: tuple[_R, ...]) -> dict[str, object]:
        return {"data": [encode(value) for value in values]}

    def read_snapshot(record: Record) -> tuple[_R, ...]:
        return parse_live_collection(record, parse)

    def argument(value: object) -> dict[str, object]:
        # Collection change events and individual record events share this codec.
        if isinstance(value, model):
            return {"shape": "record", "data": encode(value)}
        if isinstance(value, tuple) and all(
            isinstance(entry, model) for entry in cast(tuple[object, ...], value)
        ):
            return {"shape": "collection", **snapshot(cast(tuple[_R, ...], value))}
        raise EventPayloadError("invalid live event callback argument")

    def read_argument(record: Record) -> object:
        shape = record.string("shape")
        if shape == "record":
            return parse(record.record("data"))
        if shape == "collection":
            return read_snapshot(record)
        raise EventPayloadError("unsupported live callback argument shape")

    return JSONEventCodec(resource, snapshot, read_snapshot, argument, read_argument)


def drops_codec() -> JSONEventCodec[tuple[Drop, ...]]:
    return _live_codec(PollingResource.DROPS, Drop, _drop, parse_drop)


def sales_codec() -> JSONEventCodec[tuple[StoreSale, ...]]:
    return _live_codec(PollingResource.STORE_SALES, StoreSale, _sale, parse_store_sale)


def gifts_codec() -> JSONEventCodec[tuple[StoreDailyGift, ...]]:
    return _live_codec(
        PollingResource.STORE_DAILY_GIFTS, StoreDailyGift, _gift, parse_store_daily_gift
    )


def fishing_events_codec() -> JSONEventCodec[tuple[FishingEvent, ...]]:
    return _live_codec(
        PollingResource.FISHING_EVENTS,
        FishingEvent,
        _fishing_event,
        parse_fishing_event,
    )


def trending_codec() -> JSONEventCodec[TrendingGameState]:
    def snapshot(value: TrendingGameState) -> dict[str, object]:
        return {"day": value.day.isoformat(), "game": value.game}

    def read_snapshot(record: Record) -> TrendingGameState:
        day = record.datetime("day")
        if day.hour or day.minute or day.second or day.microsecond:
            raise EventPayloadError("saved streaming day must be a UTC day boundary")
        return TrendingGameState(day, record.string("game"))

    def argument(value: object) -> dict[str, object]:
        if not isinstance(value, str):
            raise EventPayloadError("expected a game name for callback arguments")
        return {"game": value}

    return JSONEventCodec(
        PollingResource.STREAM_TRENDING_GAME,
        snapshot,
        read_snapshot,
        argument,
        lambda record: record.string("game"),
    )
