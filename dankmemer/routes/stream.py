from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Optional

from dankmemer.routes.base import CachedRoute
from dankmemer.utils import parse_iso_timestamp

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class TrendingGame:
    """
    Represents the trending game data from the /stream endpoint.

    Attributes:
        lastModified (datetime): The last time the trending game was updated.
        name (str): The current trending game name.
    """

    lastModified: Optional[datetime]
    name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrendingGame":
        last_modified = data.get("lastModified")
        if isinstance(last_modified, str):
            last_modified = parse_iso_timestamp(last_modified)
        return cls(
            lastModified=last_modified,
            name=data.get("name", ""),
        )


@dataclass(frozen=True)
class StreamData:
    """
    Represents the response from the /stream endpoint.

    Attributes:
        trending_game (TrendingGame): The current trending game payload.
    """

    trending_game: TrendingGame

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamData":
        return cls(
            trending_game=TrendingGame.from_dict(data.get("trending_game", {})),
        )


class StreamRoute(CachedRoute[StreamData]):
    """
    Represents the /stream endpoint, converting raw API data into StreamData.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> StreamData:
        raw_data: Dict[str, Any] = await self.client.request("stream")
        return self._store_cache(StreamData.from_dict(raw_data))

    async def query(self) -> StreamData:
        """
        Retrieve stream metadata from the /stream endpoint.

        :return: A StreamData object.
        """
        return await self._get_data()
