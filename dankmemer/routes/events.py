from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from dankmemer.types import StringFilterType, StringType
from dankmemer.routes.base import CachedRoute, matches_string
from dankmemer.utils import IN as IN, DotDict, Fuzzy as Fuzzy, parse_iso_timestamp

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Event:
    """
    Represents an event obtained from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the event.
        name (str): The name of the event.
        imageURL (str): The URL pointing to the event's image.
        extra (Dict[str, Any]): Additional event data, typically including:
            - description (str): A description of the event.
            - last (List[Union[datetime, str]]): A list of datetime objects representing recent event occurrences.
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        extra_data = data.get("extra", {})
        if not isinstance(extra_data, DotDict):
            extra_data = DotDict(extra_data)
        # Convert the "last" field (if it exists) into a list of datetime objects.
        if "last" in extra_data:
            try:
                # Assumes last is a list of ISO8601 strings with a trailing "Z"
                extra_data["last"] = [
                    parse_iso_timestamp(ts) for ts in extra_data["last"]
                ]
            except Exception:
                # If conversion fails, keep original data.
                pass
        return cls(
            id=data.get("id"),
            name=data.get("name"),
            imageURL=data.get("imageURL"),
            extra=extra_data,
        )

    def __getattr__(self, attribute: str) -> Any:
        """
        Fallback attribute lookup: if an attribute is not found normally,
        attempt to retrieve it from the extra data.
        """
        if attribute in self.extra:
            return self.extra[attribute]
        raise AttributeError(f"'Event' object has no attribute '{attribute}'")


class EventsFilter:
    """
    Filter for /events data.

    You can filter on:
      - id: (str) exact match.
      - name: (StringFilterType) string matching (exact, fuzzy using Fuzzy, or membership using IN).
      - imageURL: (StringFilterType) matches on the event image URL.
      - description: (StringFilterType) filtering applied to extra.description.
      - limit: (int) maximum number of results returned.

    Examples:
        .. code-block:: python

            from dankmemer import EventsFilter, Fuzzy, IN

            # Exact matching on the 'name' field.
            filter_exact = EventsFilter(name="Quality Time")

            # Fuzzy matching on the 'name' field.
            filter_fuzzy = EventsFilter(name=Fuzzy("2xidlespeed", cutoff=80))

            # Membership matching using IN.
            filter_in = EventsFilter(name=IN("instanttravel"))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        description: StringFilterType = None,
        limit: Optional[int] = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.description: StringFilterType = description
        self.limit: Optional[int] = limit

    def apply(self, data: List[Event]) -> List[Event]:
        results: List[Event] = []
        for event in data:
            if self.id is not None and event.id != self.id:
                continue
            if self.name is not None and not self._matches_field(event.name, self.name):
                continue
            if self.imageURL is not None and not self._matches_field(
                event.imageURL, self.imageURL
            ):
                continue
            extra = event.extra
            if self.description is not None:
                desc = extra.get("description", "")
                if not self._matches_field(desc, self.description):
                    continue
            results.append(event)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)


class EventsRoute(CachedRoute[Dict[str, Event]]):
    """
    Represents the /events endpoint, converting raw API data into Event objects and
    providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Event]:
        raw_data: Dict[str, Any] = await self.client.request("events")
        processed: Dict[str, Event] = {}
        for key, value in raw_data.items():
            processed[key] = Event.from_dict(value)
        return self._store_cache(processed)

    async def query(self, events_filter: Optional[EventsFilter] = None) -> List[Event]:
        """
        Retrieve the list of Event objects from the /events endpoint.

        If an EventsFilter is provided, only events matching the criteria are returned.

        :param events_filter: Optional EventsFilter instance containing filtering criteria.
        :return: A list of Event objects.
        """
        raw_dict: Dict[str, Event] = await self._get_data()
        events_list: List[Event] = list(raw_dict.values())
        if events_filter is not None:
            events_list = events_filter.apply(events_list)
        return events_list

    async def iter_query(
        self, events_filter: Optional[EventsFilter] = None
    ) -> AsyncIterator[Event]:
        """
        Asynchronously iterates over Event objects from the /events endpoint.

        If an EventsFilter is provided, only events matching the filter criteria are yielded.

        :param events_filter: Optional EventsFilter instance for filtering.
        :yield: Each Event object from the query results.
        """
        raw_dict: Dict[str, Event] = await self._get_data()
        events_list: List[Event] = list(raw_dict.values())
        if events_filter is not None:
            events_list = events_filter.apply(events_list)
        for event in events_list:
            yield event
