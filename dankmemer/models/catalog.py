from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dankmemer._parsing import Record
from dankmemer.errors import ConfigurationError
from dankmemer.types import JSONValue


@dataclass(frozen=True, slots=True)
class Skin:
    """A cosmetic skin from the official catalog.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    key : str
        Text identifier used by key lookup; distinct from the display name.
    name : str
        Display name.
    type : str
        Kind of record this skin belongs to, such as an item or pet.
    reference : int | str | None
        Associated item ID (int), another resource's ID (str), or None when no
        reference is supplied.
    rarity : str
        Rarity label supplied by the API.
    image_url : str
        URL of the record's image.
    metadata : Mapping[str, JSONValue] | None
        Read-only rendering metadata, or None when not supplied. Nested arrays
        are tuples.
    """

    id: str
    key: str
    name: str
    type: str
    reference: int | str | None
    rarity: str
    image_url: str
    metadata: Mapping[str, JSONValue] | None


@dataclass(frozen=True, slots=True)
class Item:
    """An item and its catalog values.

    Attributes
    ----------
    id : int
        Integer item ID used by get_by_id and item references.
    key : str
        Text identifier used by key lookup; distinct from the display name.
    name : str
        Display name.
    type : str
        Type identifier supplied by the API.
    value : int
        Catalog value published by the API.
    sell_value : int
        Sell value published by the API.
    market_value : int
        Market value published by the API. The cached value updates when the
        item catalog is refreshed.
    flavor : str
        Short flavor text shown for this item.
    details : str
        Published item usage or description text.
    image_url : str
        URL of the record's image.
    tags : tuple[str, ...]
        Labels attached to the item.
    skins : tuple[Skin, ...]
        Cosmetic skins included in this item record.
    """

    id: int
    key: str
    name: str
    type: str
    value: int
    sell_value: int
    market_value: int
    flavor: str
    details: str
    image_url: str
    tags: tuple[str, ...]
    skins: tuple[Skin, ...]


@dataclass(frozen=True, slots=True)
class PetStats:
    """The hunger, hygiene, energy, and fun values in a pet's catalog entry.

    Attributes
    ----------
    hunger : int
        Catalog hunger value.
    hygiene : int
        Catalog hygiene value.
    energy : int
        Catalog energy value.
    fun : int
        Catalog fun value.
    """

    hunger: int
    hygiene: int
    energy: int
    fun: int


@dataclass(frozen=True, slots=True)
class Pet:
    """A pet's catalog entry, including its stats and relationship IDs.

    Attributes
    ----------
    id : str
        String identifier used to look up this record.
    name : str
        Display name.
    image_url : str
        URL of the record's image.
    cost : int
        Purchase cost published by the API.
    stats : PetStats
        Catalog stats, rather than the current stats of a user's pet.
    phrases : tuple[str, ...]
        Phrases published for this pet.
    words : tuple[str, ...]
        Words published for this pet.
    color : str
        Color string supplied by the API.
    friendly_to : tuple[str, ...]
        IDs of pets with a friendly relationship to this pet.
    hostile_to : tuple[str, ...]
        IDs of pets with a hostile relationship to this pet.
    converting_possible : bool
        Whether the API marks this pet as convertible.
    purchasable : bool
        Whether the API marks this pet as purchasable.
    """

    id: str
    name: str
    image_url: str
    cost: int
    stats: PetStats
    phrases: tuple[str, ...]
    words: tuple[str, ...]
    color: str
    friendly_to: tuple[str, ...]
    hostile_to: tuple[str, ...]
    converting_possible: bool
    purchasable: bool


@dataclass(frozen=True, slots=True)
class CommandOption:
    """A command argument, subcommand, or subcommand group.

    Attributes
    ----------
    name : str
        Argument, subcommand, or subcommand group name.
    description : str
        Published description.
    type : int
        Numeric Discord application command option type.
    required : bool | None
        Whether the argument is required, or None when the API omits this field.
    options : tuple[CommandOption, ...]
        Nested options, or an empty tuple when none are supplied.
    """

    name: str
    description: str
    type: int
    required: bool | None
    options: tuple[CommandOption, ...]


@dataclass(frozen=True, slots=True)
class Command:
    """A command's published description and options.

    Attributes
    ----------
    id : str | None
        Discord command ID, or None before the API has loaded it.
    name : str
        Command name used for name lookup.
    description : str
        Published description.
    permissions : str
        Permission value as the string supplied by the API.
    category : str
        Category identifier supplied by the API.
    hybrid : bool
        Whether the API marks this as a hybrid command.
    dms : bool
        Whether the command is marked as available in direct messages.
    cooldown : int
        Published cooldown number, retained without a unit conversion.
    donor_cooldown : int
        Published donor cooldown number, retained without a unit conversion.
    options : tuple[CommandOption, ...]
        Arguments and nested subcommands.
    """

    id: str | None
    name: str
    description: str
    permissions: str
    category: str
    hybrid: bool
    dms: bool
    cooldown: int
    donor_cooldown: int
    options: tuple[CommandOption, ...]

    def get_option(self, *path: str) -> CommandOption | None:
        """Find an option or nested subcommand option without making a request.

        Names ignore case and surrounding whitespace. For example,
        ``get_option("subcommand", "argument")`` walks two levels. An empty
        or invalid path raises ``ConfigurationError``; a missing name returns
        ``None``.
        """
        names = _option_names(path)
        options = self.options
        result: CommandOption | None = None
        for name in names:
            result = next(
                (option for option in options if option.name.casefold() == name), None
            )
            if result is None:
                return None
            options = result.options
        return result


def _option_names(path: tuple[object, ...]) -> tuple[str, ...]:
    if not path:
        raise ConfigurationError("option path must contain at least one name")
    names: list[str] = []
    for name in path:
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("option path names must be nonempty strings")
        names.append(name.strip().casefold())
    return tuple(names)


def parse_skin(record: Record) -> Skin:
    metadata = record.nullable_record("metadata")
    return Skin(
        id=record.string("id"),
        key=record.string("key"),
        name=record.string("name"),
        type=record.string("type"),
        reference=record.identifier_or_none("reference"),
        rarity=record.string("rarity"),
        image_url=record.string("imageUrl"),
        metadata=None if metadata is None else metadata.json_object(),
    )


def parse_item(record: Record) -> Item:
    return Item(
        id=record.integer("id"),
        key=record.string("key"),
        name=record.string("name"),
        type=record.string("type"),
        value=record.integer("value"),
        sell_value=record.integer("sellValue"),
        market_value=record.integer("marketValue"),
        flavor=record.string("flavor"),
        details=record.string("details"),
        image_url=record.string("imageUrl"),
        tags=record.strings("tags"),
        skins=tuple(parse_skin(skin) for skin in record.records("skins")),
    )


def parse_pet(record: Record) -> Pet:
    stats = record.record("stats")
    return Pet(
        id=record.string("id"),
        name=record.string("name"),
        image_url=record.string("imageUrl"),
        cost=record.integer("cost"),
        stats=PetStats(
            hunger=stats.integer("hunger"),
            hygiene=stats.integer("hygiene"),
            energy=stats.integer("energy"),
            fun=stats.integer("fun"),
        ),
        phrases=record.strings("phrases"),
        words=record.strings("words"),
        color=record.string("color"),
        friendly_to=record.strings("friendlyTo"),
        hostile_to=record.strings("hostileTo"),
        converting_possible=record.boolean("convertingPossible"),
        purchasable=record.boolean("purchasable"),
    )


def parse_command(record: Record) -> Command:
    return Command(
        id=record.optional_string("id"),
        name=record.string("name"),
        description=record.string("description"),
        permissions=record.string("permissions"),
        category=record.string("category"),
        hybrid=record.boolean("hybrid"),
        dms=record.boolean("dms"),
        cooldown=record.integer("cooldown"),
        donor_cooldown=record.integer("donorCooldown"),
        options=tuple(
            parse_command_option(option) for option in record.records("options")
        ),
    )


def parse_command_option(record: Record, depth: int = 0) -> CommandOption:
    if depth > 10:
        raise record.error("command options are nested too deeply")
    return CommandOption(
        name=record.string("name"),
        description=record.string("description"),
        type=record.integer("type"),
        required=record.optional_boolean("required"),
        options=tuple(
            parse_command_option(option, depth + 1)
            for option in record.optional_records("options")
        ),
    )
