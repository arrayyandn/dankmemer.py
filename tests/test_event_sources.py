# pyright: reportPrivateUsage=false

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Generic, TypeVar

import pytest
from resource_helpers import Responses, blogs

from dankmemer import (
    Blog,
    EventConfig,
    EventRecoveryError,
    LotteryResult,
    MerchantRotation,
)
from dankmemer._event_sources import (
    PublicationSource,
    PublicationState,
    SnapshotSource,
    lottery_allowed,
    lottery_changes,
    merchant_allowed,
    merchant_changes,
    publication_changes,
)
from dankmemer._observations import Emission, Observation, prepare_observation
from dankmemer.http._routes import BLOGS
from dankmemer.models.publications import parse_blog
from dankmemer.resources._base import PaginatedResource

_T = TypeVar("_T")


class Publisher(Generic[_T]):
    def __init__(
        self,
        derive: Callable[[_T | None, _T, bool], Sequence[Emission]],
        *,
        emit_initial: bool = False,
    ) -> None:
        self.derive = derive
        self.emit_initial = emit_initial
        self.current: Observation[_T] | None = None
        self.batches: list[tuple[Emission, ...]] = []
        self.full = False

    async def accept(self, observation: Observation[_T]) -> bool:
        emissions = prepare_observation(
            self.current, observation, self.derive, emit_initial=self.emit_initial
        )
        if emissions is None:
            return False
        if self.full:
            raise BufferError("full")
        if emissions:
            self.batches.append(emissions)
        self.current = observation
        return True


def drawing(hour: int, *, winnings: int = 100) -> LotteryResult:
    return LotteryResult(datetime(2026, 10, 1, hour, tzinfo=UTC), winnings, 10, 3, 1)


def rotation(day: int, *, generated_minutes: int = 0) -> MerchantRotation:
    date = datetime(2026, 10, day, tzinfo=UTC)
    return MerchantRotation(date, date + timedelta(minutes=generated_minutes), ())


@pytest.mark.asyncio
async def test_lottery_ignores_stale_and_null_results_but_delivers_corrections() -> (
    None
):
    original = drawing(1)
    corrected = drawing(1, winnings=200)
    newer = drawing(2)
    values: list[LotteryResult | None] = [
        original,
        original,
        None,
        drawing(0),
        corrected,
        newer,
    ]
    publisher = Publisher[LotteryResult | None](lottery_changes)

    async def load() -> LotteryResult | None:
        return values.pop(0)

    source = SnapshotSource[LotteryResult | None](
        load,
        publisher,
        allowed=lottery_allowed,
    )
    for _ in range(4):
        await source.poll()
    assert not publisher.batches
    await source.poll()
    await source.poll()
    assert publisher.batches == [
        (Emission("lottery_result_updated", (original, corrected)),),
        (Emission("lottery_result", (newer,)),),
    ]


@pytest.mark.asyncio
async def test_null_first_observation_allows_later_lottery_result() -> None:
    values: list[LotteryResult | None] = [None, drawing(1)]
    publisher = Publisher[LotteryResult | None](lottery_changes)

    async def load() -> LotteryResult | None:
        return values.pop(0)

    source = SnapshotSource[LotteryResult | None](
        load,
        publisher,
        allowed=lottery_allowed,
    )
    await source.poll()
    await source.poll()
    assert publisher.batches == [(Emission("lottery_result", (drawing(1),)),)]


@pytest.mark.asyncio
async def test_merchant_corrections_and_new_days_are_distinct() -> None:
    initial = rotation(1, generated_minutes=2)
    corrected = rotation(1, generated_minutes=3)
    next_day = rotation(2)
    values: list[MerchantRotation | None] = [
        initial,
        rotation(1),
        None,
        corrected,
        next_day,
        initial,
    ]
    publisher = Publisher[MerchantRotation | None](merchant_changes, emit_initial=True)

    async def load() -> MerchantRotation | None:
        return values.pop(0)

    source = SnapshotSource[MerchantRotation | None](
        load,
        publisher,
        allowed=merchant_allowed,
    )
    for _ in range(6):
        await source.poll()
    assert publisher.batches == [
        (Emission("merchant_rotation", (initial,)),),
        (Emission("merchant_rotation_updated", (initial, corrected)),),
        (Emission("merchant_rotation", (next_day,)),),
    ]


@pytest.mark.asyncio
async def test_full_queue_retains_a_detected_result_without_reading_again() -> None:
    values = [drawing(1), drawing(2), drawing(3)]
    publisher = Publisher[LotteryResult | None](lottery_changes)

    async def load() -> LotteryResult:
        return values.pop(0)

    source = SnapshotSource[LotteryResult | None](
        load,
        publisher,
        allowed=lottery_allowed,
    )
    await source.poll()
    publisher.full = True
    with pytest.raises(BufferError):
        await source.poll()
    assert source.recovering
    assert source._stream.current is not None
    assert source._stream.current.value == drawing(1)
    publisher.full = False
    await source.poll()
    assert values == [drawing(3)]
    assert publisher.batches == [(Emission("lottery_result", (drawing(2),)),)]
    assert not source.recovering


def publications(
    request: Responses,
    publisher: Publisher[PublicationState[Blog]],
    *,
    config: EventConfig | None = None,
) -> PublicationSource[Blog]:
    config = config if config is not None else EventConfig(publication_page_size=2)
    publisher.emit_initial = config.emit_initial
    return PublicationSource(
        PaginatedResource(request, BLOGS, parse_blog),
        config,
        publisher,
    )


def publication_publisher() -> Publisher[PublicationState[Blog]]:
    return Publisher(
        lambda previous, current, initial: publication_changes(
            "blog_published", previous, current
        )
    )


def published_ids(publisher: Publisher[PublicationState[Blog]]) -> list[str]:
    ids: list[str] = []
    for batch in publisher.batches:
        for emission in batch:
            value = emission.args[0]
            assert isinstance(value, Blog)
            ids.append(value.id)
    return ids


@pytest.mark.asyncio
async def test_publications_wait_for_checkpoint_and_deduplicate_shifting_offsets() -> (
    None
):
    request = Responses(
        blogs(4, 3, next_cursor=2),
        blogs(8, 7, next_cursor=2),
        blogs(7, 6, next_cursor=4),
        blogs(5, 4, next_cursor=6),
        blogs(9, 8, next_cursor=2),
    )
    publisher = publication_publisher()
    source = publications(request, publisher)
    try:
        await source.poll()
        assert not source.recovering
        assert not publisher.batches
        await source.poll()
        await source.poll()
        assert source.recovering
        assert not publisher.batches
        await source.poll()
        assert not source.recovering
        assert published_ids(publisher) == ["5", "6", "7", "8"]
        await source.poll()
        assert published_ids(publisher) == ["5", "6", "7", "8", "9"]
        assert [call.cursor for call in request.calls] == [0, 0, 2, 4, 0]
        assert [call.limit for call in request.calls] == [2] * 5
    finally:
        await source._reader._close()


@pytest.mark.asyncio
async def test_failed_recovery_page_preserves_cursor_staging_and_checkpoint() -> None:
    request = Responses(
        blogs(3, 2),
        blogs(6, 5, next_cursor=2),
        RuntimeError("unavailable"),
        blogs(4, 3),
    )
    publisher = publication_publisher()
    source = publications(request, publisher)
    try:
        await source.poll()
        await source.poll()
        with pytest.raises(RuntimeError):
            await source.poll()
        assert source._cursor == 2
        assert list(source._staged) == ["6", "5"]
        assert not publisher.batches
        await source.poll()
        assert published_ids(publisher) == ["4", "5", "6"]
        assert [call.cursor for call in request.calls] == [0, 0, 2, 2]
    finally:
        await source._reader._close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [blogs(2, 1), blogs(5, 4, next_cursor=4)])
async def test_missing_checkpoint_or_repeated_page_never_publishes_partial_history(
    ending: object,
) -> None:
    request = Responses(blogs(3), blogs(5, 4, next_cursor=2), ending)
    publisher = publication_publisher()
    source = publications(request, publisher)
    try:
        await source.poll()
        await source.poll()
        with pytest.raises(EventRecoveryError) as captured:
            await source.poll()
        assert captured.value.checkpoint_id == "3"
        assert source._stream.current is not None
        assert source._stream.current.value.head_id == "3"
        assert not publisher.batches
        assert not source.recovering
    finally:
        await source._reader._close()


@pytest.mark.asyncio
async def test_recovery_cap_leaves_baseline_and_requires_later_retry() -> None:
    request = Responses(blogs(3), blogs(6, 5, next_cursor=2), blogs(4, 3))
    publisher = publication_publisher()
    source = publications(
        request,
        publisher,
        config=EventConfig(publication_page_size=2, max_staged_publications=2),
    )
    try:
        await source.poll()
        await source.poll()
        with pytest.raises(EventRecoveryError, match="max_staged_publications"):
            await source.poll()
        assert source._stream.current is not None
        assert source._stream.current.value.head_id == "3"
        assert not publisher.batches
    finally:
        await source._reader._close()


@pytest.mark.asyncio
async def test_completed_publication_batch_is_retained_when_queue_is_full() -> None:
    request = Responses(blogs(1), blogs(3, 2, next_cursor=2), blogs(1))
    publisher = publication_publisher()
    source = publications(request, publisher)
    try:
        await source.poll()
        await source.poll()
        publisher.full = True
        with pytest.raises(BufferError):
            await source.poll()
        assert source._stream.current is not None
        assert source._stream.current.value.head_id == "1"
        assert source.recovering
        publisher.full = False
        await source.poll()
        assert len(request.calls) == 3
        assert published_ids(publisher) == ["2", "3"]
        assert not source.recovering
    finally:
        await source._reader._close()


@pytest.mark.asyncio
async def test_emit_initial_publishes_only_first_page_oldest_first() -> None:
    request = Responses(blogs(3, 2, next_cursor=2))
    publisher = publication_publisher()
    source = publications(
        request,
        publisher,
        config=EventConfig(emit_initial=True, publication_page_size=2),
    )
    try:
        await source.poll()
        assert published_ids(publisher) == ["2", "3"]
        assert len(request.calls) == 1
        assert not source.recovering
    finally:
        await source._reader._close()


@pytest.mark.asyncio
async def test_empty_initial_publication_collection_recovers_all_new_entries() -> None:
    request = Responses(blogs(), blogs(3, 2, next_cursor=2), blogs(1))
    publisher = publication_publisher()
    source = publications(request, publisher)
    try:
        await source.poll()
        await source.poll()
        assert not publisher.batches
        await source.poll()
        assert published_ids(publisher) == ["1", "2", "3"]
    finally:
        await source._reader._close()
