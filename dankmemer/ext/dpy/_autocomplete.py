from __future__ import annotations

import asyncio
from typing import Self

from discord import app_commands

from dankmemer.models import Item
from dankmemer.resources import Items
from dankmemer.resources._queries import boolean, text

__all__ = ("ItemAutocomplete",)


class ItemAutocomplete:
    """Item choices for a discord.py slash command's autocomplete callback.

    Create the helper during startup with :meth:`create`. It uses the
    supplied item catalog's cache and does not own a session or background
    task. Choice labels display names; their values identify the item even
    when several items have the same name. Use :meth:`resolve` in the command
    handler to accept a selected choice or an exact name typed by the user.
    """

    def __init__(self, items: Items) -> None:
        self._items = items

    @classmethod
    async def create(cls, items: Items, *, preload: bool = True) -> Self:
        """Prepare suggestions, loading the item catalog by default.

        ``preload=False`` skips loading here; :meth:`choices` fetches a
        missing or expired catalog when it is first needed. Other lookups
        on ``items`` share this load and its normal one-hour cache.
        """
        boolean(preload, "preload")
        items._check_access()  # pyright: ignore[reportPrivateUsage]
        if preload:
            await items._catalog()  # pyright: ignore[reportPrivateUsage]
        return cls(items)

    async def choices(
        self, current: str, *, fetch: bool = True, fuzzy: bool = False
    ) -> list[app_commands.Choice[str]]:
        """Return up to 25 choices for the text currently being typed.

        Empty input returns names in alphabetical order. The default search
        matches substrings; ``fuzzy=True`` also tolerates spelling mistakes.
        Labels are shortened to Discord's 100-character limit.

        Fetch a missing or expired catalog by default. With ``fetch=False``,
        return no choices when the cache is unavailable. A load taking over
        two seconds also returns no choices, leaving its shared catalog load
        available to other callers. Request errors propagate to the caller.
        """
        try:
            async with asyncio.timeout(2):
                matches = await self._items._suggest(  # pyright: ignore[reportPrivateUsage]
                    current, fuzzy=fuzzy, fetch=fetch
                )
        except TimeoutError:
            return []
        return [
            app_commands.Choice(name=item.name[:100], value=f"id:{item.id}")
            for item in matches
        ]

    async def resolve(self, value: str) -> Item | None:
        """Look up a selected choice or an exact, case-insensitive item name.

        Return ``None`` for an unknown name or ID. A duplicate typed name
        raises :class:`~dankmemer.AmbiguousLookupError`; selecting a choice
        avoids that ambiguity. Uses the same catalog cache as :meth:`choices`.
        """
        self._items._check_access()  # pyright: ignore[reportPrivateUsage]
        value = text(value, "value")
        if value.startswith("id:"):
            identifier = value[3:]
            if (
                len(identifier) > 97
                or not identifier.isascii()
                or not identifier.isdecimal()
            ):
                return None
            return await self._items.get_by_id(int(identifier))
        return await self._items.get(value)
