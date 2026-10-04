from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Generic, Protocol, TypeVar

from ._observations import Emission, Observation
from ._settle import settle
from .config import EventConfig
from .errors import EventRecoveryError
from .models.activities import GlobalBoost, LotteryResult, MerchantRotation
from .models.publications import Blog, Changelog
from .pagination import Page
from .resources._base import PaginatedResource

_T = TypeVar("_T")
_P = TypeVar("_P", Blog, Changelog)


class Observer(Protocol[_T]):
    @property
    def current(self) -> Observation[_T] | None: ...

    async def accept(self, observation: Observation[_T]) -> bool: ...


class Source(Protocol):
    @property
    def recovering(self) -> bool: ...

    @property
    def observed_at(self) -> float | None: ...

    async def poll(self) -> None: ...

    def reset(self) -> None: ...


class SnapshotSource(Generic[_T]):
    """Read complete live snapshots and retain batches rejected by delivery."""

    def __init__(
        self,
        load: Callable[[], Awaitable[_T]],
        observer: Observer[_T],
        *,
        allowed: Callable[[_T, _T], bool] | None = None,
        timestamp: Callable[[_T], float | None] | None = None,
        tolerance: timedelta = timedelta(0),
    ) -> None:
        self._load = load
        self._tolerance = tolerance
        self._allowed = allowed
        self._timestamp = timestamp
        self._stream: Observer[_T] = observer
        self._pending: Observation[_T] | None = None

    @property
    def recovering(self) -> bool:
        return self._pending is not None

    @property
    def observed_at(self) -> float | None:
        current = self._stream.current
        if current is None or self._timestamp is None:
            return None
        return self._timestamp(current.value)

    def reset(self) -> None:
        self._pending = None

    async def poll(self) -> None:
        if self._pending is None:
            value = await self._load()
            current = self._stream.current
            if current is not None:
                # Keep baseline timestamps for request-derived jitter.
                value = settle(current.value, value, self._tolerance)
            if (
                current is not None
                and self._allowed is not None
                and not self._allowed(current.value, value)
            ):
                return
            self._pending = Observation(value, complete=True)
        # Keep a detected change until the whole callback batch is admitted.
        await self._stream.accept(self._pending)
        self._pending = None


def boost_changes(
    previous: tuple[GlobalBoost, ...] | None,
    current: tuple[GlobalBoost, ...],
    initial: bool,
) -> tuple[Emission, ...]:
    before = () if previous is None else previous
    if initial or before != current:
        return (Emission("global_boosts_changed", (before, current)),)
    return ()


def lottery_allowed(
    previous: LotteryResult | None, current: LotteryResult | None
) -> bool:
    return previous is None or (
        current is not None and current.drawn_at >= previous.drawn_at
    )


def lottery_changes(
    previous: LotteryResult | None, current: LotteryResult | None, initial: bool
) -> tuple[Emission, ...]:
    if current is None:
        return ()
    if previous is None or current.drawn_at > previous.drawn_at:
        return (Emission("lottery_result", (current,)),)
    if current != previous:
        return (Emission("lottery_result_updated", (previous, current)),)
    return ()


def merchant_allowed(
    previous: MerchantRotation | None, current: MerchantRotation | None
) -> bool:
    return previous is None or (
        current is not None
        and current.date >= previous.date
        and (
            current.date > previous.date
            or current.generated_at >= previous.generated_at
        )
    )


def merchant_changes(
    previous: MerchantRotation | None, current: MerchantRotation | None, initial: bool
) -> tuple[Emission, ...]:
    if current is None:
        return ()
    if previous is None or current.date > previous.date:
        return (Emission("merchant_rotation", (current,)),)
    if current != previous:
        return (Emission("merchant_rotation_updated", (previous, current)),)
    return ()


@dataclass(frozen=True, slots=True)
class PublicationState(Generic[_P]):
    head_id: str | None
    recent_ids: tuple[str, ...]
    new_entries: tuple[_P, ...]


def publication_allowed(
    previous: PublicationState[_P], current: PublicationState[_P]
) -> bool:
    if previous.head_id is None:
        return True
    return current.head_id is not None and (
        current.head_id == previous.head_id
        or current.head_id not in previous.recent_ids
    )


def publication_changes(
    event: str, previous: PublicationState[_P] | None, current: PublicationState[_P]
) -> tuple[Emission, ...]:
    known: set[str] = set() if previous is None else set(previous.recent_ids)
    return tuple(
        Emission(event, (entry,))
        for entry in current.new_entries
        if entry.id not in known
    )


class PublicationSource(Generic[_P]):
    """Recover new publications one page at a time before moving the head ID."""

    def __init__(
        self,
        reader: PaginatedResource[_P],
        config: EventConfig,
        observer: Observer[PublicationState[_P]],
    ) -> None:
        self._reader: PaginatedResource[_P] = reader
        self._config = config
        self._stream: Observer[PublicationState[_P]] = observer
        self._cursor = 0
        self._head_id: str | None = None
        self._staged: dict[str, _P] = {}
        self._pending: Observation[PublicationState[_P]] | None = None

    @property
    def recovering(self) -> bool:
        return self._cursor != 0 or self._pending is not None

    @property
    def observed_at(self) -> float | None:
        return None

    def _reset_recovery(self) -> None:
        self._cursor = 0
        self._head_id = None
        self._staged.clear()

    def reset(self) -> None:
        self._pending = None
        self._reset_recovery()

    async def poll(self) -> None:
        if self._pending is None:
            page = await self._reader.fetch(
                page_size=self._config.publication_page_size, cursor=self._cursor
            )
            try:
                self._read_page(page)
            except EventRecoveryError:
                # Restart from the head later; the accepted checkpoint stays put.
                self._reset_recovery()
                raise
        if self._pending is not None:
            await self._stream.accept(self._pending)
            self._pending = None
            self._reset_recovery()

    def _read_page(self, page: Page[_P]) -> None:
        current = self._stream.current
        if current is None:
            head_id = page.items[0].id if page.items else None
            self._pending = Observation(
                PublicationState(
                    head_id,
                    tuple(entry.id for entry in page.items),
                    tuple(reversed(page.items)),
                ),
                complete=True,
            )
            return

        previous: PublicationState[_P] = current.value
        head_id = (
            self._head_id
            if self._cursor
            else (page.items[0].id if page.items else None)
        )
        staged: dict[str, _P] = dict(self._staged)
        known = set(previous.recent_ids)
        found = False
        for entry in page.items:
            if entry.id == previous.head_id:
                found = True
                break
            if entry.id not in known:
                staged.setdefault(entry.id, entry)
        if len(staged) > self._config.max_staged_publications:
            raise EventRecoveryError(
                "publication recovery exceeds max_staged_publications",
                checkpoint_id=previous.head_id,
            )
        if not found and page.next_cursor is not None:
            if self._cursor and len(staged) == len(self._staged):
                raise EventRecoveryError(
                    "publication recovery page contains no new IDs",
                    checkpoint_id=previous.head_id,
                )
            self._staged = staged
            self._head_id = head_id
            self._cursor = page.next_cursor
            return
        if not found and previous.head_id is not None:
            raise EventRecoveryError(
                "previous publication checkpoint is absent from the collection",
                checkpoint_id=previous.head_id,
            )

        recent: tuple[str, ...] = tuple(dict.fromkeys((*staged, *previous.recent_ids)))
        self._pending = Observation(
            PublicationState(
                head_id,
                recent[: self._config.max_staged_publications],
                tuple(reversed(tuple(staged.values()))),
            ),
            complete=True,
        )
