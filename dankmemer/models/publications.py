from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dankmemer._parsing import Record


@dataclass(frozen=True, slots=True)
class Blog:
    """A published blog summary.

    Attributes
    ----------
    id : str
        Publication ID used for lookup and event deduplication.
    title : str
        Published article title.
    description : str
        Article summary. The full article is available at url.
    created_at : datetime
        Publication time, as an aware UTC datetime.
    url : str
        Link to the published page.
    """

    id: str
    title: str
    description: str
    created_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class Changelog:
    """A published changelog summary with a timezone-aware UTC datetime.

    Attributes
    ----------
    id : str
        Publication ID used for lookup and event deduplication.
    title : str
        Published changelog title. The full changelog is available at url.
    created_at : datetime
        Publication time, as an aware UTC datetime.
    url : str
        Link to the published page.
    """

    id: str
    title: str
    created_at: datetime
    url: str


def parse_blog(record: Record) -> Blog:
    return Blog(
        id=record.string("id"),
        title=record.string("title"),
        description=record.string("description"),
        created_at=record.datetime("createdAt"),
        url=record.string("url"),
    )


def parse_changelog(record: Record) -> Changelog:
    return Changelog(
        id=record.string("id"),
        title=record.string("title"),
        created_at=record.datetime("createdAt"),
        url=record.string("url"),
    )
