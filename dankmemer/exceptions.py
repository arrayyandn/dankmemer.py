class DankMemerException(Exception):
    """Base exception for all errors."""
    pass


class UnsupportedRouteException(DankMemerException):
    """Exception raised for routes unavailable through the default Gwapes API."""

    supported_routes = ("items",)
    rule_url = "https://dankmemer.lol/rules"

    def __init__(self, route: str) -> None:
        self.route = route
        super().__init__(
            f"Route {route!r} is unavailable because DankAlert is permanently "
            "offline. dankmemer.py currently supports only the 'items' route "
            "through the limited-field Gwapes API. Dank Memer Rule 13 restricts "
            "external bots and services that scrape Dank Memer data; review "
            f"{self.rule_url}."
        )


class DankMemerHTTPException(DankMemerException):
    """Base exception for errors returned by an HTTP data source."""
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
