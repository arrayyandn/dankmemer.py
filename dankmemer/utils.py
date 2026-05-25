from typing import Any
from datetime import datetime

class Fuzzy:
    """
    Represents a fuzzy match criterion.

    :param value: The string value to match.
    :param cutoff: The minimum matching ratio (0-100) required for a successful fuzzy match.
                   Defaults to 80.
    
    Example:
        name = Fuzzy("Melmsie", cutoff=75)
    """

    def __init__(self, value: str, cutoff: int = 80) -> None:
        self.value: str = value
        self.cutoff: int = cutoff

    def __repr__(self) -> str:
        return f"Fuzzy({self.value!r}, cutoff={self.cutoff})"
    

class IN:
    """
    Represents a filter for string membership.

    Use this filter to check if a target string matches any of the provided patterns.

    :param patterns: The string / strings to match.

    Example:
        name = IN("Melmsie", "Mel")
    """
    def __init__(self, *patterns: str) -> None:
        self.patterns = patterns

    def __repr__(self) -> str:
        return f"IN({', '.join(repr(v) for v in self.patterns)})"


class Above:
    """
    Represents a filter to match numeric fields above a given threshold.
    
    :param threshold: The value to use as a threshold.

    Example:
        netValue = Above(10000)
    """
    def __init__(self, threshold: int | float) -> None:
        self.threshold = threshold

    def __repr__(self) -> str:
        return f"Above({self.threshold})"


class Below:
    """
    Represents a filter to match numeric fields below a given threshold.
    
    :param threshold: The value to use as a threshold.

    Example:
        netValue = Below(100000)
    """
    def __init__(self, threshold: int | float) -> None:
        self.threshold = threshold

    def __repr__(self) -> str:
        return f"Below({self.threshold})"


class Range:
    """
    Represents a filter for numeric fields within an inclusive range.
    
    :param threshold: The value to use as a threshold.

    Example:
        netValue = Range(100, 100000)
    """
    def __init__(self, low: int | float, high: int | float) -> None:
        self.low = low
        self.high = high

    def __repr__(self) -> str:
        return f"Range({self.low}, {self.high})"


class DotDict(dict):
    """A dictionary subclass to allow attribute-style access to dictionary items."""

    def __getattr__(self, attr: str) -> Any:
        try:
            value = self[attr]
        except KeyError:
            raise AttributeError(f"'DotDict' object has no attribute '{attr}'")
        # If the value is a dictionary, wrap it in DotDict for recursive access.
        if isinstance(value, dict) and not isinstance(value, DotDict):
            value = DotDict(value)
        return value

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

def parse_iso_timestamp(ts: str) -> datetime:
    """
    Parse an ISO8601 timestamp string with a trailing 'Z' indicating UTC.
    Converts it into a datetime object with tzinfo set to UTC.
    """
    # Replace trailing 'Z' with '+00:00'
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
