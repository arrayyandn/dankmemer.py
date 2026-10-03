class DankMemerError(Exception):
    """Base exception for errors raised by the SDK."""

    pass


class ConfigurationError(DankMemerError, ValueError):
    """An option or request argument is invalid or unsupported."""

    pass


class LifecycleError(DankMemerError, RuntimeError):
    """An operation is incompatible with the client's current state."""

    pass


class AmbiguousLookupError(DankMemerError):
    """An exact lookup matched multiple records.

    ``candidate_ids`` identifies the matching records. Use resource filters
    or an ID lookup to choose one explicitly.
    """

    def __init__(
        self, *, name: str, path: str, candidate_ids: tuple[int | str, ...]
    ) -> None:
        super().__init__("multiple records match the requested lookup")
        self.name = name
        self.path = path
        self.candidate_ids = candidate_ids


class DankMemerHTTPError(DankMemerError):
    """An HTTP error response or a locally exhausted request allowance.

    ``status_code`` is the HTTP status or 429 for a local daily limit.
    ``path`` is the registered route template, without a user ID.
    ``retry_after_seconds`` is available when a delay is known.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        path: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.retry_after_seconds = retry_after_seconds


class BadRequest(DankMemerHTTPError):
    """The API returned HTTP 400."""

    pass


class AuthenticationError(DankMemerHTTPError):
    """The API returned HTTP 401."""

    pass


class Forbidden(DankMemerHTTPError):
    """The API returned HTTP 403."""

    pass


class NotFound(DankMemerHTTPError):
    """The API returned HTTP 404."""

    pass


class RateLimited(DankMemerHTTPError):
    """The API returned HTTP 429 or the local daily allowance ran out."""

    pass


class ServerError(DankMemerHTTPError):
    """The API returned an HTTP 5xx response."""

    pass


class DankMemerConnectionError(DankMemerError):
    """An HTTP request failed because of a connection error.

    ``path`` is the registered route template when one is available.
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class DankMemerTimeoutError(DankMemerConnectionError):
    """A request exceeded its network or total request deadline."""

    pass


class DankMemerResponseError(DankMemerError):
    """A response is too large, empty, invalid JSON, or has invalid fields.

    ``path`` is the registered route template when one is available.
    ``field`` identifies a response field when one is known.
    """

    def __init__(
        self, message: str, *, path: str | None = None, field: str | None = None
    ) -> None:
        super().__init__(message)
        self.path = path
        self.field = field


class PaginationError(DankMemerResponseError):
    """A paginated response cannot be processed."""

    pass


class EventRecoveryError(DankMemerError):
    """Publication recovery cannot safely reach its previous checkpoint.

    ``checkpoint_id`` is the last accepted publication ID. Recovery can fail
    when that entry disappears, pages stop making progress, or the configured
    staging bound is exceeded. The checkpoint is retained and no partial batch
    is delivered. The client records this error in ``last_poll_errors``.
    """

    def __init__(self, message: str, *, checkpoint_id: str | None) -> None:
        super().__init__(message)
        self.checkpoint_id = checkpoint_id


class EventPayloadError(DankMemerError):
    """Saved event data has an invalid or unsupported payload format.

    Invalid checkpoints prevent event startup. Invalid callback payloads stay
    pending and pause their subscription. No saved records are deleted.
    """

    pass
