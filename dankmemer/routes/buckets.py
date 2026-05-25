from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Union

from dankmemer.types import (
    IntegerType,
    NumericFilterType,
    StringFilterType,
    StringType,
)
from dankmemer.routes.base import CachedRoute, matches_numeric, matches_string
from dankmemer.utils import (
    IN as IN,
    Above as Above,
    Below as Below,
    DotDict,
    Fuzzy as Fuzzy,
    Range as Range,
)

if TYPE_CHECKING:
    from dankmemer.client import DankMemerClient


@dataclass(frozen=True)
class Bucket:
    """
    Represents a bucket obtained from the DankAlert API.

    Attributes:
        id (str): The unique identifier for the bucket.
        name (str): The name of the bucket.
        imageURL (str): The URL of the bucket's image.
        extra (Dict[str, Any]): Additional bucket data.
            - flavor (str): A descriptive flavor text.
            - size (int): The size of the bucket.
    """

    id: str
    name: str
    imageURL: str
    extra: DotDict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bucket":
        extra_data = data.get("extra", {})
        if not isinstance(extra_data, DotDict):
            extra_data = DotDict(extra_data)
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
        raise AttributeError(f"'Bucket' object has no attribute '{attribute}'")


class BucketsFilter:
    """
    Filter for /buckets data.

    You can filter on:
      - id: (str) exact match.
      - name: (StringFilterType) string matching (exact, fuzzy via Fuzzy, or membership using IN).
      - imageURL: (StringFilterType) match on the bucket's image URL.
      - flavor: (StringFilterType) filtering applied to extra.flavor.
      - size: (NumericFilterType) numeric filtering on extra.size (supports exact value, tuple range, or interfaces Above, Below, Range).
      - limit: (int) maximum number of results returned.

    Examples:
        .. code-block:: python

            from dankmemer import BucketsFilter, Fuzzy, IN, Above, Below, Range

            # Exact string matching for the 'name' field.
            filter_exact = BucketsFilter(name="Golden Bucket")

            # Fuzzy matching for the 'name' field.
            filter_fuzzy = BucketsFilter(name=Fuzzy("golden", cutoff=80))

            # Membership matching using IN for the 'name' field.
            filter_in = BucketsFilter(name=IN("bucket"))

            # Numeric filtering: filtering 'size' for an exact match.
            filter_numeric = BucketsFilter(size=50)

            # Numeric filtering with a tuple range.
            filter_range = BucketsFilter(size=(10, 50))

            # Numeric filtering using interfaces.
            filter_above = BucketsFilter(size=Above(20))
            filter_below = BucketsFilter(size=Below(60))
            filter_range_interface = BucketsFilter(size=Range(25, 75))
    """

    def __init__(
        self,
        id: StringType = None,
        name: StringFilterType = None,
        imageURL: StringFilterType = None,
        flavor: StringFilterType = None,
        size: NumericFilterType = None,
        limit: IntegerType = None,
    ) -> None:
        self.id: StringType = id
        self.name: StringFilterType = name
        self.imageURL: StringFilterType = imageURL
        self.flavor: StringFilterType = flavor
        self.size: NumericFilterType = size
        self.limit: IntegerType = limit

    def apply(self, data: List[Bucket]) -> List[Bucket]:
        results: List[Bucket] = []
        for bucket in data:
            if self.id is not None and bucket.id != self.id:
                continue
            if self.name is not None and not self._matches_field(
                bucket.name, self.name
            ):
                continue
            if self.imageURL is not None and not self._matches_field(
                bucket.imageURL, self.imageURL
            ):
                continue

            extra = bucket.extra
            if self.flavor is not None:
                flavor_val = extra.get("flavor", "")
                if not self._matches_field(flavor_val, self.flavor):
                    continue
            if self.size is not None:
                s = extra.get("size")
                if s is None or not self._matches_numeric(s, self.size):
                    continue
            results.append(bucket)
        if self.limit is not None:
            results = results[: self.limit]
        return results

    def _matches_field(self, field_value: str, filter_val: StringFilterType) -> bool:
        return matches_string(field_value, filter_val)

    def _matches_numeric(
        self, field_value: Union[int, float], filter_val: NumericFilterType
    ) -> bool:
        return matches_numeric(field_value, filter_val)


class BucketsRoute(CachedRoute[Dict[str, Bucket]]):
    """
    Represents the /buckets endpoint, converting raw API data into Bucket objects and
    providing route-specific filtering.
    """

    def __init__(self, client: "DankMemerClient", cache_ttl: timedelta | None) -> None:
        super().__init__(client, cache_ttl)

    async def _fetch(self) -> Dict[str, Bucket]:
        raw_data: Dict[str, Any] = await self.client.request("buckets")
        processed: Dict[str, Bucket] = {}
        for key, value in raw_data.items():
            processed[key] = Bucket.from_dict(value)
        return self._store_cache(processed)

    async def query(
        self, bucket_filter: Optional[BucketsFilter] = None
    ) -> List[Bucket]:
        """
        Retrieve the list of Bucket objects from the /buckets endpoint.

        If a BucketsFilter is provided, only buckets matching the criteria are returned.

        :param bucket_filter: Optional BucketsFilter instance for filtering criteria.
        :return: A list of Bucket objects.
        """
        raw_dict: Dict[str, Bucket] = await self._get_data()
        buckets_list: List[Bucket] = list(raw_dict.values())
        if bucket_filter is not None:
            buckets_list = bucket_filter.apply(buckets_list)
        return buckets_list

    async def iter_query(
        self, bucket_filter: Optional[BucketsFilter] = None
    ) -> AsyncIterator[Bucket]:
        """
        Asynchronously iterate over Bucket objects from the /buckets endpoint.

        :param bucket_filter: Optional BucketsFilter instance for filtering.
        :yield: Each Bucket object that matches the filter.
        """
        raw_dict: Dict[str, Bucket] = await self._get_data()
        buckets_list: List[Bucket] = list(raw_dict.values())
        if bucket_filter is not None:
            buckets_list = bucket_filter.apply(buckets_list)
        for bucket in buckets_list:
            yield bucket
