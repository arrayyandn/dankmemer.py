import json
from datetime import UTC, datetime

import pytest

from dankmemer import (
    Blog,
    Changelog,
    EventPayloadError,
    GlobalBoost,
    LotteryResult,
    MerchantRotation,
)
from dankmemer._event_codecs import (
    blogs_codec,
    boosts_codec,
    changelogs_codec,
    lottery_codec,
    merchant_codec,
)
from dankmemer._event_sources import PublicationState
from dankmemer._parsing import Record
from dankmemer.models.activities import parse_merchant_rotation


def test_boost_snapshot_and_collection_arguments_round_trip() -> None:
    codec = boosts_codec()
    before = (GlobalBoost("coins", 2.5, datetime(2026, 10, 1, 12, tzinfo=UTC)),)
    after = before + (GlobalBoost("xp", 3.0, datetime(2026, 10, 1, 13, tzinfo=UTC)),)
    assert codec.decode_snapshot(codec.encode_snapshot(after)) == after
    assert codec.decode_args(codec.encode_args((before, after))) == (before, after)
    assert codec.decode_snapshot(codec.encode_snapshot(())) == ()


def test_lottery_snapshot_null_and_both_event_shapes_round_trip() -> None:
    codec = lottery_codec()
    before = LotteryResult(
        datetime(2026, 10, 1, 1, 2, 3, 123456, tzinfo=UTC), 100, 10, 3, 1
    )
    after = LotteryResult(before.drawn_at, 200, 20, 4, 2)
    assert codec.decode_snapshot(codec.encode_snapshot(None)) is None
    assert codec.decode_snapshot(codec.encode_snapshot(after)) == after
    assert codec.decode_args(codec.encode_args((after,))) == (after,)
    assert codec.decode_args(codec.encode_args((before, after))) == (before, after)
    with pytest.raises(EventPayloadError):
        codec.encode_args((None,))


def test_merchant_preserves_unknown_reward_data_without_false_changes() -> None:
    data: dict[str, object] = {
        "date": "2026-10-01T00:00:00Z",
        "generatedAt": "2026-10-01T00:02:03.125Z",
        "trades": [
            {
                "position": "top",
                "max": 1,
                "reward": {
                    "type": "future-reward",
                    "futureField": {"tags": ["a", "b"], "enabled": True},
                },
                "forReward": {"type": "item", "item": 12, "quantity": 2},
            }
        ],
    }
    rotation: MerchantRotation = parse_merchant_rotation(
        Record(data, path="/merchant-trades")
    )
    codec = merchant_codec()
    restored = codec.decode_snapshot(codec.encode_snapshot(rotation))
    assert restored == rotation
    assert restored is not None
    assert restored.trades[0].reward.data == rotation.trades[0].reward.data
    assert codec.decode_args(codec.encode_args((rotation, rotation))) == (
        rotation,
        rotation,
    )
    assert codec.decode_snapshot(codec.encode_snapshot(None)) is None


def test_publication_checkpoints_and_callbacks_round_trip_with_unicode() -> None:
    date = datetime(2026, 10, 1, tzinfo=UTC)
    blog = Blog("blog-1", "Fish café 🎣", "Summary", date, "https://example.test/blog")
    changelog = Changelog("change-1", "Changes", date, "https://example.test/changelog")
    blog_state = PublicationState("blog-1", ("blog-1", "blog-0"), (blog,))
    changelog_state = PublicationState("change-1", ("change-1",), (changelog,))
    blog_codec = blogs_codec()
    changelog_codec = changelogs_codec()
    assert (
        blog_codec.decode_snapshot(blog_codec.encode_snapshot(blog_state)) == blog_state
    )
    assert (
        changelog_codec.decode_snapshot(
            changelog_codec.encode_snapshot(changelog_state)
        )
        == changelog_state
    )
    assert blog_codec.decode_args(blog_codec.encode_args((blog,))) == (blog,)
    assert changelog_codec.decode_args(changelog_codec.encode_args((changelog,))) == (
        changelog,
    )
    assert blog_codec.decode_snapshot(
        blog_codec.encode_snapshot(PublicationState[Blog](None, (), ()))
    ) == PublicationState[Blog](None, (), ())


@pytest.mark.parametrize(
    "patch",
    [
        {"version": 2},
        {"version": True},
        {"format": "another-sdk"},
        {"resource": "merchant_trades"},
        {"kind": "args"},
        {"value": {"data": {"drawnAt": "invalid"}}},
    ],
)
def test_invalid_saved_format_is_rejected(patch: dict[str, object]) -> None:
    codec = lottery_codec()
    body: dict[str, object] = json.loads(codec.encode_snapshot(None))
    body.update(patch)
    with pytest.raises(EventPayloadError):
        codec.decode_snapshot(json.dumps(body).encode())


@pytest.mark.parametrize("payload", [b"not JSON", b"\xff", b"[]", b"null"])
def test_invalid_json_is_not_loaded_as_python_code(payload: bytes) -> None:
    with pytest.raises(EventPayloadError):
        lottery_codec().decode_snapshot(payload)


def test_publication_checkpoint_requires_consistent_unique_ids() -> None:
    codec = blogs_codec()
    body: dict[str, object] = json.loads(
        codec.encode_snapshot(PublicationState[Blog](None, (), ()))
    )
    body["value"] = {"headId": "missing", "recentIds": ["known"], "newEntries": []}
    with pytest.raises(EventPayloadError):
        codec.decode_snapshot(json.dumps(body).encode())
