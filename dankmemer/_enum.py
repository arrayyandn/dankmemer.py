from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import ClassVar, cast


class _EnumMeta(type):
    """Build sealed enum classes from unique uppercase string definitions.

    Members retain their definition order and are looked up by value when the
    enum class is called. This implementation does not inherit from stdlib
    :class:`enum.Enum` or :class:`str`.
    """

    __members__: Mapping[str, _Enum]
    _enum_values: Mapping[str, _Enum]

    def __new__(
        mcls: type[_EnumMeta],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **kwargs: object,
    ) -> _EnumMeta:
        if any(getattr(base, "_enum_sealed", False) for base in bases):
            raise TypeError("SDK enum classes cannot be subclassed")

        definitions: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, value in namespace.items():
            if not key.isupper() or key.startswith("_"):
                continue
            if not isinstance(value, str):
                raise TypeError(f"{name}.{key} must have a string value")
            if value in seen:
                raise ValueError(f"duplicate SDK enum value in {name}: {value!r}")
            seen.add(value)
            definitions.append((key, value))

        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        enum_cls = cast("type[_Enum]", cls)
        members: dict[str, _Enum] = {}
        values: dict[str, _Enum] = {}
        for key, value in definitions:
            member = object.__new__(enum_cls)
            object.__setattr__(member, "_name", key)
            object.__setattr__(member, "_value", value)
            type.__setattr__(cls, key, member)
            members[key] = member
            values[value] = member

        type.__setattr__(cls, "__members__", MappingProxyType(members))
        type.__setattr__(cls, "_enum_values", MappingProxyType(values))
        type.__setattr__(cls, "_enum_sealed", bool(definitions))
        return cls

    def __call__(cls, value: object) -> _Enum:
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not a valid {cls.__name__}")
        try:
            return cls._enum_values[value]
        except KeyError:
            raise ValueError(f"{value!r} is not a valid {cls.__name__}") from None

    def __getitem__(cls, name: str) -> _Enum:
        return cast(type[_Enum], cls).__members__[name]

    def __iter__(cls) -> Iterator[_Enum]:
        return iter(cast(type[_Enum], cls).__members__.values())

    def __len__(cls) -> int:
        return len(cast(type[_Enum], cls).__members__)

    def __setattr__(cls, name: str, value: object) -> None:
        if cls.__dict__.get("_enum_sealed", False):
            raise TypeError(f"{cls.__name__} is sealed")
        super().__setattr__(name, value)

    def __delattr__(cls, name: str) -> None:
        if cls.__dict__.get("_enum_sealed", False):
            raise TypeError(f"{cls.__name__} is sealed")
        super().__delattr__(name)


class _Enum(metaclass=_EnumMeta):
    """An immutable member whose equality is limited to its own enum class."""

    __slots__ = ("_name", "_value")

    _name: str
    _value: str
    __members__: ClassVar[Mapping[str, _Enum]]
    _enum_values: ClassVar[Mapping[str, _Enum]]
    _enum_sealed: ClassVar[bool]

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> str:
        return self._value

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("SDK enum members are immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("SDK enum members are immutable")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"<{type(self).__name__}.{self.name}: {self.value!r}>"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _Enum)
            and type(self) is type(other)
            and self.value == other.value
        )

    def __hash__(self) -> int:
        return hash((type(self), self.value))
