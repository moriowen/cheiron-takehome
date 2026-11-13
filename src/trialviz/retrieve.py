"""Retrieval: get the records (or counts) a chart needs from the registry, exactly when the
API's rate limit allows it (D32, D33).

The API allows about 1 request/s, and one request returns at most 1000 records. So each
method below is chosen by how many requests it needs:
- `registry_count`: one count per bucket of a fixed-value dimension (phase, status, ...).
  Exact at any size. Each count also returns a few records of that bucket, to cite.
- `all_records`: every matching record, page by page. Exact, used up to `max_records`.
- `sample`: the first `sample_records` in API relevance order, when the query matches more
  than `max_records` and the chart needs records. Counts are then of the sample, and the
  response says so.
"""

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from trialviz.ctgov.client import MAX_PAGE_SIZE, CTGovClient, StudyQuery
from trialviz.registry import nct_id

log = logging.getLogger(__name__)

CountMethod = Literal["registry_count", "all_records", "sample"]
CITATIONS_PER_BUCKET = 5  # records fetched per bucket count, to cite (= builder MAX_CITATIONS)


@dataclass(frozen=True)
class Retrieved:
    studies: list[dict]
    total_count: int  # all registry matches for the query
    truncated: bool = False  # True when `studies` is a cut of the matches and counts use it
    method: CountMethod = "all_records"
    bucket_counts: dict[str, int] | None = None  # registry count per bucket key

    @property
    def records_used(self) -> int:
        """Trials the counts cover: every match for exact methods, else the fetched ones."""
        return self.total_count if self.method == "registry_count" else len(self.studies)


class TTLCache:
    """Thread-safe cache with a lifetime per entry and a size bound (least recently used out)."""

    def __init__(self, ttl_s: float, max_entries: int = 512):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._entries: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return None
            if time.monotonic() - hit[0] >= self.ttl_s:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return hit[1]

    def put(self, key, value) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class Retriever:
    def __init__(
        self,
        client: CTGovClient,
        *,
        max_records: int = 20_000,
        sample_records: int = 3000,
        cache: TTLCache | None = None,
    ):
        self._client = client
        self._max_records = max_records
        self._sample_records = sample_records
        self._cache = cache or TTLCache(3600)
        self._inflight: dict[Any, threading.Lock] = {}
        self._inflight_lock = threading.Lock()

    def retrieve(
        self,
        query: StudyQuery,
        fields: tuple[str, ...],
        buckets: tuple[tuple[str, str], ...] = (),
    ) -> Retrieved:
        """Counts for a chart, by the method that needs the fewest requests while exact."""
        started = time.perf_counter()
        total = self._count(query)
        pages = math.ceil(total / MAX_PAGE_SIZE)
        if buckets and (len(buckets) < pages or total > self._max_records):
            out = self._bucket_counts(query, fields, buckets, total)
        elif total <= self._max_records:
            r = self._fetch(query, fields, None)
            out = Retrieved(r.studies, r.total_count)
        else:
            r = self._fetch(query, fields, self._sample_records)
            out = Retrieved(r.studies, r.total_count, truncated=r.truncated, method="sample")
        log.info(
            "%s: %d matches, %d records in %.1fs: %s",
            out.method,
            total,
            len(out.studies),
            time.perf_counter() - started,
            query.to_params(),
        )
        return out

    def citations(self, query: StudyQuery, fields: tuple[str, ...], n: int) -> Retrieved:
        """The registry count, plus the first `n` records to cite (for a `total` answer)."""
        r = self._fetch(query, fields, n)
        return Retrieved(r.studies, r.total_count, truncated=r.truncated, method="registry_count")

    def count(self, query: StudyQuery) -> int:
        """Registry matches for a query (cached)."""
        return self._count(query)

    def suggest(self, text: str, dictionary: str) -> list[str]:
        """The registry's autocomplete names for `text` (cached; see CTGovClient.suggest)."""
        key = ("suggest", text.lower(), dictionary)
        return self._cached(key, lambda: self._client.suggest(text, dictionary))

    def _bucket_counts(self, query, fields, buckets, total: int) -> Retrieved:
        studies: dict[str, dict] = {}  # by NCT ID: a multi-valued trial can be in 2 samples
        counts: dict[str, int] = {}
        for key, clause in buckets:
            r = self._fetch(query.and_advanced(clause), fields, CITATIONS_PER_BUCKET)
            counts[key] = r.total_count
            for s in r.studies:
                studies.setdefault(nct_id(s), s)
        return Retrieved(
            list(studies.values()), total, method="registry_count", bucket_counts=counts
        )

    def _count(self, query: StudyQuery) -> int:
        key = ("count", tuple(sorted(query.to_params().items())))
        return self._cached(key, lambda: self._client.count(query))

    def _fetch(self, query: StudyQuery, fields: tuple[str, ...], limit: int | None):
        key = ("fetch", tuple(sorted(query.to_params().items())), fields, limit)
        return self._cached(
            key, lambda: self._client.fetch_studies(query, fields=fields, limit=limit)
        )

    def _cached(self, key, load: Callable[[], Any]) -> Any:
        """Cache hit, or load once: concurrent requests for the same key wait for one call
        instead of each sending it (single flight)."""
        if (hit := self._cache.get(key)) is not None:
            return hit
        with self._inflight_lock:
            lock = self._inflight.setdefault(key, threading.Lock())
        with lock:
            if (hit := self._cache.get(key)) is None:
                hit = load()
                self._cache.put(key, hit)
        with self._inflight_lock:
            self._inflight.pop(key, None)
        return hit
