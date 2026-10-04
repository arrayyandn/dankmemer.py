from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import timedelta
from typing import TypeAlias

from .enums import CoordinationMode, EventDelivery
from .errors import ConfigurationError


def _number(value: object, name: str, *, minimum: float, strict: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ConfigurationError(f"{name} must be a finite number") from None
    if not math.isfinite(number) or (number <= minimum if strict else number < minimum):
        relation = "greater than" if strict else "at least"
        raise ConfigurationError(f"{name} must be finite and {relation} {minimum:g}")
    return number


def _integer(value: object, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"{name} must be an integer of at least {minimum}")
    return value


def _event_delivery(value: object) -> None:
    if not isinstance(value, EventDelivery):
        raise ConfigurationError("delivery must be EventDelivery")


def _optional_positive(value: object | None, name: str) -> None:
    if value is not None:
        _number(value, name, minimum=0, strict=True)


# Polls are at least a minute apart, so a larger tolerance could hide updates.
_MAX_TIMESTAMP_TOLERANCE = timedelta(minutes=1)


def _timestamp_tolerance(value: object) -> None:
    if (
        not isinstance(value, timedelta)
        or value < timedelta(0)
        or value > _MAX_TIMESTAMP_TOLERANCE
    ):
        raise ConfigurationError(
            "timestamp_tolerance must be a timedelta from zero to one minute"
        )


def _positive_timedelta(value: object) -> None:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise ConfigurationError("cache ttl must be a positive timedelta")


def _header_component(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ConfigurationError(f"{name} must be a nonempty header-safe string")
    if len(value) > 128 or any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ConfigurationError(f"{name} must be a nonempty header-safe string")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationIdentity:
    """Application name and optional version sent in the ``User-Agent`` header."""

    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        _header_component(self.name, "application.name")
        if self.version is not None:
            _header_component(self.version, "application.version")


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestConfig:
    """Timeout and response size limits for HTTP requests.

    ``timeout_seconds`` bounds one network attempt. ``total_timeout_seconds``
    also covers admission waits, retries, and backoff. Pass ``None`` for either
    timeout to disable that limit. ``max_response_bytes`` limits the body read
    from a successful response.
    """

    timeout_seconds: float | None = 30.0
    total_timeout_seconds: float | None = 90.0
    max_response_bytes: int = 10_485_760

    def __post_init__(self) -> None:
        _optional_positive(self.timeout_seconds, "timeout_seconds")
        _optional_positive(self.total_timeout_seconds, "total_timeout_seconds")
        _integer(self.max_response_bytes, "max_response_bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryConfig:
    """Retry settings for connection failures and retryable HTTP responses.

    ``max_attempts`` includes the first request. Backoff starts at
    ``initial_backoff_seconds``. ``max_backoff_seconds`` caps the exponential
    base, but jitter and a server ``Retry-After`` value may extend the actual
    delay. ``jitter_ratio`` adds up to that fraction of the base delay.
    HTTP 503 responses use at least five seconds of base backoff.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 5.0
    max_backoff_seconds: float = 30.0
    jitter_ratio: float = 0.2
    retry_on_rate_limit: bool = True
    retry_on_server_error: bool = True

    def __post_init__(self) -> None:
        _integer(self.max_attempts, "max_attempts")
        initial = _number(
            self.initial_backoff_seconds,
            "initial_backoff_seconds",
            minimum=0,
            strict=True,
        )
        maximum = _number(
            self.max_backoff_seconds, "max_backoff_seconds", minimum=0, strict=True
        )
        if maximum < initial:
            raise ConfigurationError(
                "max_backoff_seconds must be at least initial_backoff_seconds"
            )
        _number(self.jitter_ratio, "jitter_ratio", minimum=0, strict=False)
        if self.jitter_ratio > 1:
            raise ConfigurationError("jitter_ratio must be at most 1")
        if (
            type(self.retry_on_rate_limit) is not bool
            or type(self.retry_on_server_error) is not bool
        ):
            raise ConfigurationError("retry flags must be bool")


@dataclass(frozen=True, slots=True, init=False)
class IntervalPolling:
    """A polling interval of at least 60 seconds.

    Pass exactly one of ``seconds``, ``minutes``, or ``hours``. The interval is
    validated and stored as a :class:`datetime.timedelta`.
    """

    interval: timedelta

    def __init__(
        self,
        *,
        seconds: float | None = None,
        minutes: float | None = None,
        hours: float | None = None,
    ) -> None:
        selected = [
            (name, value, multiplier)
            for name, value, multiplier in (
                ("seconds", seconds, 1),
                ("minutes", minutes, 60),
                ("hours", hours, 3600),
            )
            if value is not None
        ]
        if len(selected) != 1:
            raise ConfigurationError("provide exactly one interval unit")
        name, value, multiplier = selected[0]
        duration = _number(value, name, minimum=0, strict=True) * multiplier
        if not math.isfinite(duration) or duration < 60:
            raise ConfigurationError(
                "automatic polling interval must be at least 60 seconds"
            )
        try:
            object.__setattr__(self, "interval", timedelta(seconds=duration))
        except OverflowError:
            raise ConfigurationError(
                "automatic polling interval is too large"
            ) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class HourlyPolling:
    """Poll around each UTC hour and retry when its result has not arrived.

    ``offset_seconds`` and ``retry_seconds`` are measured in seconds;
    ``retry_window_minutes`` and ``reconcile_minutes`` are measured in minutes.
    Once the current hour's result is observed, wait for the next hour.
    Otherwise retry inside the window, then check at the reconciliation
    interval. All automatic request attempts remain at least a minute apart.
    """

    offset_seconds: float = 15.0
    retry_seconds: float = 60.0
    retry_window_minutes: float = 5.0
    reconcile_minutes: float = 5.0

    def __post_init__(self) -> None:
        _calendar_values(self, period_seconds=3600)


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyPolling:
    """Poll around UTC midnight and retry when the day's rotation is late.

    ``offset_seconds`` and ``retry_seconds`` are measured in seconds;
    ``retry_window_minutes`` and ``reconcile_minutes`` are measured in minutes.
    Once the current day's rotation is observed, wait for the next midnight.
    Otherwise retry inside the window, then use the reconciliation interval.
    """

    offset_seconds: float = 15.0
    retry_seconds: float = 60.0
    retry_window_minutes: float = 5.0
    reconcile_minutes: float = 15.0

    def __post_init__(self) -> None:
        _calendar_values(self, period_seconds=86400)


def _calendar_values(
    policy: HourlyPolling | DailyPolling, *, period_seconds: int
) -> None:
    offset = _number(policy.offset_seconds, "offset_seconds", minimum=0, strict=False)
    if offset >= period_seconds:
        raise ConfigurationError("calendar offset must be inside its period")
    _number(policy.retry_seconds, "retry_seconds", minimum=60, strict=False)
    _number(
        policy.retry_window_minutes, "retry_window_minutes", minimum=0, strict=False
    )
    _number(policy.reconcile_minutes, "reconcile_minutes", minimum=1, strict=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class DisabledPolling:
    """Disable automatic polling for a resource, even when it has listeners."""

    pass


IntervalPolicy: TypeAlias = IntervalPolling | DisabledPolling
LotteryPolicy: TypeAlias = IntervalPolling | HourlyPolling | DisabledPolling
DailyPolicy: TypeAlias = IntervalPolling | DailyPolling | DisabledPolling


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingConfig:
    """Per-resource timing for automatic event polling.

    ``None`` uses the library default: one minute for global boosts, one hour
    for blogs and changelogs, an hourly schedule for lottery results, and a
    daily schedule for merchant rotations, daily gifts, and streaming games.
    Drops, sales, and fishing events also default to one minute.
    Only resources with listeners are polled.

    ``startup_jitter_seconds`` spreads initial polls over that many seconds.
    """

    drops: IntervalPolicy | None = None
    global_boosts: IntervalPolicy | None = None
    store_sales: IntervalPolicy | None = None
    store_daily_gifts: DailyPolicy | None = None
    stream_trending_game: DailyPolicy | None = None
    fishing_events: IntervalPolicy | None = None
    blogs: IntervalPolicy | None = None
    changelogs: IntervalPolicy | None = None
    lottery: LotteryPolicy | None = None
    merchant_trades: DailyPolicy | None = None
    startup_jitter_seconds: float = 0.0

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.name == "startup_jitter_seconds":
                continue
            allowed: tuple[type, ...] = (IntervalPolling, DisabledPolling)
            if item.name == "lottery":
                allowed += (HourlyPolling,)
            elif item.name in {
                "merchant_trades",
                "store_daily_gifts",
                "stream_trending_game",
            }:
                allowed += (DailyPolling,)
            value = getattr(self, item.name)
            if value is not None and not isinstance(value, allowed):
                raise ConfigurationError(
                    f"{item.name} has an unsupported polling policy"
                )
        _number(
            self.startup_jitter_seconds,
            "startup_jitter_seconds",
            minimum=0,
            strict=False,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CoordinationConfig:
    """Choose independent or shared ownership of polling and subscriptions.

    Shared mode requires durable delivery and a ``CoordinatedEventStore``.
    ``application_id`` is the application's public dashboard ID, shared by all
    its keys. It binds one SQLite file or PostgreSQL schema to that application.
    Tokens are never stored.

    ``lease_seconds`` bounds how long ownership survives without renewal.
    ``heartbeat_seconds`` renews membership and ownership; ``check_seconds`` controls
    database checks for pending deliveries and poller takeover. These database
    intervals do not change the API's minimum polling interval.
    Heartbeats must be at most one third of the lease duration, and database
    checks must be no slower than heartbeats. Defaults are 30, 10, and 1 seconds.
    Shared clients must agree on event settings and enabled polling schedules.
    """

    mode: CoordinationMode = CoordinationMode.INDEPENDENT
    application_id: str | None = None
    lease_seconds: float = 30.0
    heartbeat_seconds: float = 10.0
    check_seconds: float = 1.0

    def __post_init__(self) -> None:
        _coordination_mode(self.mode)
        if self.mode is CoordinationMode.SHARED:
            if (
                not isinstance(self.application_id, str)
                or not self.application_id.strip()
            ):
                raise ConfigurationError("shared coordination requires application_id")
        elif self.application_id is not None:
            raise ConfigurationError("application_id requires shared coordination")
        lease = _number(self.lease_seconds, "lease_seconds", minimum=0, strict=True)
        heartbeat = _number(
            self.heartbeat_seconds, "heartbeat_seconds", minimum=0, strict=True
        )
        check = _number(self.check_seconds, "check_seconds", minimum=0, strict=True)
        if heartbeat * 3 > lease:
            raise ConfigurationError(
                "lease_seconds must allow at least three heartbeats"
            )
        if check > heartbeat:
            raise ConfigurationError("check_seconds cannot exceed heartbeat_seconds")


def _coordination_mode(value: object) -> None:
    if not isinstance(value, CoordinationMode):
        raise ConfigurationError("mode must be CoordinationMode")


@dataclass(frozen=True, slots=True, kw_only=True)
class EventConfig:
    """Delivery, initial observations, and bounds for event callbacks.

    With no previous baseline, the first successful observation establishes
    one without calling listeners. ``emit_initial=True`` also emits the
    available initial state; for publications this means entries in the first
    fetched page. A saved baseline is restored rather than treated as initial,
    and this setting does not prevent pending durable callbacks from replaying.

    ``max_pending_deliveries`` counts queued and running listener calls across
    the client. ``publication_page_size`` is the number of entries requested
    in each recovery cycle. ``max_staged_publications`` bounds records held
    while searching for the previous checkpoint; its default holds ten full
    pages. Exceeding a bound raises an error without advancing that checkpoint.
    These bound pending work and recovery memory, not API quotas or history.

    ``timestamp_tolerance`` is the largest difference between a polled
    timestamp and its saved value that is not reported as a change. Some
    timestamps, such as a global boost's end, are derived per request and vary
    by a millisecond between identical responses. A timestamp within the
    tolerance keeps its saved value, so slow drift is still measured from the
    baseline. The default is one second; ``timedelta(0)`` compares exactly.

    ``BEST_EFFORT`` uses an in-memory store and discards pending calls on close.
    Callback failures are logged and that call is not retried. ``DURABLE``
    requires a persistent ``event_store`` on the client. Failed calls pause
    their subscription and stay pending until retried or replayed on restart.
    Shared mode keeps pauses in the store; restarting does not clear them.
    A successful call can be repeated if its acknowledgement was not saved.
    The pending-delivery bound applies to all pending calls in the chosen store,
    including subscriptions that are not currently registered.
    """

    emit_initial: bool = False
    delivery: EventDelivery = EventDelivery.BEST_EFFORT
    max_pending_deliveries: int = 1_000
    publication_page_size: int = 100
    max_staged_publications: int = 1_000
    timestamp_tolerance: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        _event_delivery(self.delivery)
        if type(self.emit_initial) is not bool:
            raise ConfigurationError("emit_initial must be bool")
        _integer(self.max_pending_deliveries, "max_pending_deliveries")
        _integer(self.publication_page_size, "publication_page_size")
        if self.publication_page_size > 100:
            raise ConfigurationError("publication_page_size must be at most 100")
        _integer(self.max_staged_publications, "max_staged_publications")
        if self.max_staged_publications < self.publication_page_size:
            raise ConfigurationError(
                "max_staged_publications must hold at least one publication page"
            )
        _timestamp_tolerance(self.timestamp_tolerance)


@dataclass(frozen=True, slots=True, kw_only=True)
class NoCache:
    """Disable retained cache entries for a resource.

    Concurrent callers can still share one request or catalog load. Each
    later access reloads data after that load finishes.
    """

    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class TTLCache:
    """A time-to-live cache policy with a maximum number of entries.

    ``ttl`` must be positive and ``max_entries`` must be at least one.
    The lifetime starts after a successful load and does not extend on reads.
    Expiry is checked on access; no background task refreshes entries. A
    complete reference catalog counts as one entry, regardless of its record
    count. Individually fetched pages each count as one entry. The least
    recently used entry is evicted when this per-resource bound is reached.
    """

    ttl: timedelta
    max_entries: int = 128

    def __post_init__(self) -> None:
        _positive_timedelta(self.ttl)
        _integer(self.max_entries, "max_entries")


CachePolicy: TypeAlias = NoCache | TTLCache


@dataclass(frozen=True, slots=True, kw_only=True)
class FishingCacheConfig:
    """Cache policy choices for fishing resources.

    A ``None`` field uses the client's one-hour reference cache default.
    Supply ``NoCache()`` to disable caching for an individual category.
    """

    creatures: CachePolicy | None = None
    locations: CachePolicy | None = None
    npcs: CachePolicy | None = None
    tools: CachePolicy | None = None
    baits: CachePolicy | None = None
    buckets: CachePolicy | None = None
    skills: CachePolicy | None = None

    def __post_init__(self) -> None:
        _validate_cache_fields(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheConfig:
    """Per-resource cache policy choices.

    A ``None`` field uses the SDK default: one hour for reference catalogs,
    including items, and no caching for publications or live resources.
    A policy applies to one resource's pages and complete catalog,
    not to individual matching records. These defaults are library choices,
    independent of the API's own cache durations.
    """

    items: CachePolicy | None = None
    pets: CachePolicy | None = None
    skins: CachePolicy | None = None
    commands: CachePolicy | None = None
    fishing: FishingCacheConfig | None = None
    global_boosts: CachePolicy | None = None
    blogs: CachePolicy | None = None
    changelogs: CachePolicy | None = None
    store_sales: CachePolicy | None = None
    store_daily_gifts: CachePolicy | None = None
    stream_trending_game: CachePolicy | None = None
    lottery: CachePolicy | None = None
    merchant_trades: CachePolicy | None = None
    drops: CachePolicy | None = None
    fishing_events: CachePolicy | None = None

    def __post_init__(self) -> None:
        _validate_cache_fields(self)


def _validate_cache_fields(config: FishingCacheConfig | CacheConfig) -> None:
    for item in fields(config):
        value = getattr(config, item.name)
        if value is None:
            continue
        allowed = (
            (FishingCacheConfig,) if item.name == "fishing" else (NoCache, TTLCache)
        )
        if not isinstance(value, allowed):
            raise ConfigurationError(f"{item.name} has an unsupported cache policy")
