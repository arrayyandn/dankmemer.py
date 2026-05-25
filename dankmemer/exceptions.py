class DankMemerException(Exception):
    """Base exception for all errors."""
    pass


class DankMemerHTTPException(DankMemerException):
    """Base exception for HTTP errors from Dank Alert's API."""
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        route: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.route = route


class DankMemerConnectionException(DankMemerException):
    """Exception raised when the client cannot reach the API."""

    def __init__(self, message: str, route: str | None = None) -> None:
        super().__init__(message)
        self.route = route


class DankMemerResponseException(DankMemerHTTPException):
    """Exception raised when the API response cannot be decoded."""
    pass


class NotFoundException(DankMemerHTTPException):
    """Exception raised when a requested resource is not found (HTTP 404)."""
    pass


class ServerErrorException(DankMemerHTTPException):
    """Exception raised for server errors (HTTP 5xx)."""
    pass


class RateLimitException(DankMemerHTTPException):
    """Exception raised when rate limited (HTTP 429)."""
    pass


class BadRequestException(DankMemerHTTPException):
    """Exception raised for a bad request (HTTP 400)."""
    pass
