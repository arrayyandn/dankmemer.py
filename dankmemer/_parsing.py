from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TypeVar, cast

from .errors import DankMemerResponseError
from .types import JSONValue

_T = TypeVar("_T")


class Record:
    def __init__(self, value: object, *, path: str, field: str = "data") -> None:
        self.path = path
        self.field = field
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in cast(dict[object, object], value)
        ):
            raise self.error("expected an object")
        self._data = cast(dict[str, object], value)

    def error(self, message: str, key: str | None = None) -> DankMemerResponseError:
        field = self.field if key is None else self._field(key)
        return DankMemerResponseError(message, path=self.path, field=field)

    def _field(self, key: str) -> str:
        return f"{self.field}.{key}" if self.field else key

    def value(self, key: str) -> object:
        try:
            return self._data[key]
        except KeyError:
            raise self.error("required field is missing", key) from None

    def string(self, key: str) -> str:
        value = self.value(key)
        if not isinstance(value, str):
            raise self.error("expected a string", key)
        return value

    def integer(self, key: str) -> int:
        value = self.value(key)
        if type(value) is not int:
            raise self.error("expected an integer", key)
        return value

    def boolean(self, key: str) -> bool:
        value = self.value(key)
        if type(value) is not bool:
            raise self.error("expected a boolean", key)
        return value

    def number(self, key: str) -> float:
        value = self.value(key)
        if type(value) not in (int, float):
            raise self.error("expected a finite number", key)
        try:
            number = float(cast(int | float, value))
        except OverflowError:
            raise self.error("expected a finite number", key) from None
        if not math.isfinite(number):
            raise self.error("expected a finite number", key)
        return number

    def optional_boolean(self, key: str) -> bool | None:
        if key not in self._data:
            return None
        return self.boolean(key)

    def optional_string(self, key: str) -> str | None:
        if key not in self._data:
            return None
        return self.string(key)

    def optional_integer(self, key: str) -> int | None:
        if key not in self._data:
            return None
        return self.integer(key)

    def optional_number(self, key: str) -> float | None:
        if key not in self._data:
            return None
        return self.number(key)

    def nullable_string(self, key: str) -> str | None:
        return None if self.value(key) is None else self.string(key)

    def nullable_number(self, key: str) -> float | None:
        return None if self.value(key) is None else self.number(key)

    def identifier_or_none(self, key: str) -> str | int | None:
        value = self.value(key)
        if value is None or isinstance(value, str) or type(value) is int:
            return value
        raise self.error("expected a string, integer, or null", key)

    def datetime(self, key: str) -> datetime:
        value = self.string(key)
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
        raise self.error("expected a date and time with a timezone", key)

    def record(self, key: str) -> Record:
        return Record(self.value(key), path=self.path, field=self._field(key))

    def nullable_record(self, key: str) -> Record | None:
        if self.value(key) is None:
            return None
        return self.record(key)

    def array(self, key: str) -> tuple[object, ...]:
        value = self.value(key)
        if not isinstance(value, list):
            raise self.error("expected an array", key)
        return tuple(cast(list[object], value))

    def records(self, key: str) -> tuple[Record, ...]:
        return tuple(
            Record(value, path=self.path, field=f"{self._field(key)}[{index}]")
            for index, value in enumerate(self.array(key))
        )

    def optional_records(self, key: str) -> tuple[Record, ...]:
        if key not in self._data:
            return ()
        return self.records(key)

    def strings(self, key: str) -> tuple[str, ...]:
        values = self.array(key)
        if not all(isinstance(value, str) for value in values):
            raise self.error("expected an array of strings", key)
        return cast(tuple[str, ...], values)

    def integers(self, key: str) -> tuple[int, ...]:
        values = self.array(key)
        if not all(type(value) is int for value in values):
            raise self.error("expected an array of integers", key)
        return cast(tuple[int, ...], values)

    def mapping(self, key: str, parse: Callable[[Record], _T]) -> Mapping[str, _T]:
        record = self.record(key)
        return MappingProxyType(
            {
                name: parse(Record(value, path=self.path, field=record._field(name)))
                for name, value in record._data.items()
            }
        )

    def json(self, key: str) -> JSONValue:
        return _freeze_json(self.value(key), path=self.path, field=self._field(key))

    def json_object(self) -> Mapping[str, JSONValue]:
        return MappingProxyType(
            {
                key: _freeze_json(value, path=self.path, field=self._field(key))
                for key, value in self._data.items()
            }
        )


def _freeze_json(value: object, *, path: str, field: str, depth: int = 0) -> JSONValue:
    # Opaque metadata must not let a caller mutate a stored snapshot.
    if depth > 100:
        raise DankMemerResponseError(
            "JSON value is nested too deeply", path=path, field=field
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return tuple(
            _freeze_json(item, path=path, field=f"{field}[{index}]", depth=depth + 1)
            for index, item in enumerate(cast(list[object], value))
        )
    if isinstance(value, dict) and all(
        isinstance(key, str) for key in cast(dict[object, object], value)
    ):
        return MappingProxyType(
            {
                key: _freeze_json(
                    item, path=path, field=f"{field}.{key}", depth=depth + 1
                )
                for key, item in cast(dict[str, object], value).items()
            }
        )
    raise DankMemerResponseError("expected a JSON value", path=path, field=field)
