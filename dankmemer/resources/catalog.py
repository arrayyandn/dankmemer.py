from __future__ import annotations

from collections.abc import Callable

from dankmemer._clock import Clock
from dankmemer.config import CachePolicy
from dankmemer.errors import ConfigurationError, DankMemerResponseError
from dankmemer.http._routes import COMMANDS, ITEMS, PETS, SKINS
from dankmemer.models.catalog import (
    Command,
    Item,
    Pet,
    Skin,
    parse_command,
    parse_item,
    parse_pet,
    parse_skin,
)

from ._base import Requester
from ._catalog import CatalogResource
from ._queries import (
    integer,
    integer_range,
    model,
    normalise,
    optional_boolean,
    optional_text,
    text,
    within,
)
from ._queries import (
    tags as normalised_tags,
)


class Items(CatalogResource[Item, int]):
    """Look up, search, filter, or paginate the official item catalog.

    Name, ID, key, and filter operations share one complete catalog. Item
    values and metadata expire together under the configured cache policy.
    """

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            ITEMS,
            parse_item,
            int,
            key=lambda item: item.key,
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def get_by_key(self, key: str, *, refresh: bool = False) -> Item | None:
        """Look up an exact, case-sensitive item key using the shared catalog."""
        return await self._get_by_key(key, refresh=refresh)

    async def filter(
        self,
        *,
        name: str | None = None,
        type: str | None = None,
        tags: tuple[str, ...] | None = None,
        min_value: int | None = None,
        max_value: int | None = None,
        min_sell_value: int | None = None,
        max_sell_value: int | None = None,
        min_market_value: int | None = None,
        max_market_value: int | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[Item, ...]:
        """Filter items locally, requiring every supplied condition to match.

        Names, types, and tags match case-insensitively. All requested tags
        must be present. Numeric bounds are inclusive. Results retain API
        order and default to at most 100; ``None`` returns all matches.
        """
        name = optional_text(name, "name")
        item_type = optional_text(type, "type")
        required_tags = normalised_tags(tags)
        integer_range(min_value, max_value, "value")
        integer_range(min_sell_value, max_sell_value, "sell_value")
        integer_range(min_market_value, max_market_value, "market_value")
        return await self._select(
            lambda item: (
                (name is None or normalise(item.name) == name)
                and (item_type is None or normalise(item.type) == item_type)
                and all(
                    tag in {normalise(value) for value in item.tags}
                    for tag in required_tags
                )
                and within(item.value, min_value, max_value)
                and within(item.sell_value, min_sell_value, max_sell_value)
                and within(item.market_value, min_market_value, max_market_value)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )


class Pets(CatalogResource[Pet, str]):
    """Look up, search, filter, or paginate pet catalog entries."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request, PETS, parse_pet, str, cache=cache, clock=clock, guard=guard
        )

    async def filter(
        self,
        *,
        name: str | None = None,
        min_cost: int | None = None,
        max_cost: int | None = None,
        purchasable: bool | None = None,
        converting_possible: bool | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[Pet, ...]:
        """Filter pets by exact name, inclusive cost bounds, or catalog flags."""
        name = optional_text(name, "name")
        integer_range(min_cost, max_cost, "cost")
        optional_boolean(purchasable, "purchasable")
        optional_boolean(converting_possible, "converting_possible")
        return await self._select(
            lambda pet: (
                (name is None or normalise(pet.name) == name)
                and within(pet.cost, min_cost, max_cost)
                and (purchasable is None or pet.purchasable is purchasable)
                and (
                    converting_possible is None
                    or pet.converting_possible is converting_possible
                )
            ),
            max_limit=max_limit,
            refresh=refresh,
        )

    async def friendly_to(
        self, pet: Pet | str, *, refresh: bool = False
    ) -> tuple[Pet, ...]:
        """Resolve friendly pet IDs from a model or an exact pet name.

        An unknown name returns an empty tuple. An unresolved relationship
        ID raises a response error. This operation loads the catalog once.
        """
        return await self._relationships(pet, friendly=True, refresh=refresh)

    async def hostile_to(
        self, pet: Pet | str, *, refresh: bool = False
    ) -> tuple[Pet, ...]:
        """Resolve hostile pet IDs from a model or an exact pet name."""
        return await self._relationships(pet, friendly=False, refresh=refresh)

    async def _relationships(
        self, pet: Pet | str, *, friendly: bool, refresh: bool
    ) -> tuple[Pet, ...]:
        name = text(pet, "pet") if isinstance(pet, str) else None
        pet_model = None if name is not None else model(pet, Pet, "pet")
        catalog = await self._catalog(refresh=refresh)
        if name is not None:
            pet_model = self._unique(catalog.names.get(normalise(name), ()), name)
            if pet_model is None:
                return ()
        assert pet_model is not None
        ids = pet_model.friendly_to if friendly else pet_model.hostile_to
        models: list[Pet] = []
        for record_id in ids:
            related = catalog.ids.get(record_id)
            if related is None:
                raise DankMemerResponseError(
                    "related pet is absent from the catalog",
                    path=PETS.template,
                    field="friendlyTo" if friendly else "hostileTo",
                )
            models.append(related)
        return tuple(models)


class Skins(CatalogResource[Skin, str]):
    """Look up skins by name or filter their type, rarity, and reference."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            SKINS,
            parse_skin,
            str,
            key=lambda skin: skin.key,
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def get(
        self,
        name: str,
        *,
        type: str | None = None,
        reference: int | str | None = None,
        refresh: bool = False,
    ) -> Skin | None:
        """Look up a name, optionally disambiguating with type and reference.

        Raises ``AmbiguousLookupError`` when more than one skin still matches.
        ``reference=None`` leaves the reference unrestricted.
        """
        name = text(name, "name")
        skin_type = optional_text(type, "type")
        self._validate_reference(reference)
        catalog = await self._catalog(refresh=refresh)
        matches = tuple(
            skin
            for skin in catalog.names.get(normalise(name), ())
            if (skin_type is None or normalise(skin.type) == skin_type)
            and (reference is None or skin.reference == reference)
        )
        return self._unique(matches, name)

    async def get_by_key(self, key: str, *, refresh: bool = False) -> Skin | None:
        """Look up an exact skin key, raising if the key is shared by skins."""
        return await self._get_by_key(key, refresh=refresh)

    @staticmethod
    def _validate_reference(reference: object | None) -> None:
        if reference is not None and not (
            isinstance(reference, str) or type(reference) is int
        ):
            raise ConfigurationError("reference must be an integer, string, or None")

    async def filter(
        self,
        *,
        name: str | None = None,
        type: str | None = None,
        rarity: str | None = None,
        reference: int | str | None = None,
        has_reference: bool | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[Skin, ...]:
        """Filter skins with all supplied conditions, in catalog order.

        ``has_reference=False`` selects skins whose reference is null.
        ``reference=None`` leaves the reference unrestricted.
        """
        name = optional_text(name, "name")
        skin_type = optional_text(type, "type")
        rarity = optional_text(rarity, "rarity")
        optional_boolean(has_reference, "has_reference")
        self._validate_reference(reference)
        return await self._select(
            lambda skin: (
                (name is None or normalise(skin.name) == name)
                and (skin_type is None or normalise(skin.type) == skin_type)
                and (rarity is None or normalise(skin.rarity) == rarity)
                and (reference is None or skin.reference == reference)
                and (
                    has_reference is None
                    or (skin.reference is not None) is has_reference
                )
            ),
            max_limit=max_limit,
            refresh=refresh,
        )

    async def for_item(
        self, item: Item | int, *, refresh: bool = False
    ) -> tuple[Skin, ...]:
        """Return all item skins for an item model or integer item ID."""
        item_id = integer(item.id if isinstance(item, Item) else item, "item")
        return await self.filter(
            type="item", reference=item_id, max_limit=None, refresh=refresh
        )

    async def for_pet(self, pet: Pet, *, refresh: bool = False) -> tuple[Skin, ...]:
        """Return all pet skins associated with a pet model."""
        pet = model(pet, Pet, "pet")
        return await self.filter(
            type="pet", reference=pet.id, max_limit=None, refresh=refresh
        )


class Commands(CatalogResource[Command, str]):
    """Look up command names or filter published command flags."""

    def __init__(
        self,
        request: Requester,
        *,
        cache: CachePolicy | None = None,
        clock: Clock | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            request,
            COMMANDS,
            parse_command,
            str,
            identity=lambda command: command.name,
            cache=cache,
            clock=clock,
            guard=guard,
        )

    async def filter(
        self,
        *,
        category: str | None = None,
        hybrid: bool | None = None,
        dms: bool | None = None,
        max_limit: int | None = 100,
        refresh: bool = False,
    ) -> tuple[Command, ...]:
        """Filter by an exact category and optional hybrid or DM support flags."""
        category = optional_text(category, "category")
        optional_boolean(hybrid, "hybrid")
        optional_boolean(dms, "dms")
        return await self._select(
            lambda command: (
                (category is None or normalise(command.category) == category)
                and (hybrid is None or command.hybrid is hybrid)
                and (dms is None or command.dms is dms)
            ),
            max_limit=max_limit,
            refresh=refresh,
        )
