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
import time
from dataclasses import dataclass, field
from typing import Literal

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


@dataclass
class TTLCache:
    ttl_s: float
    entries: dict = field(default_factory=dict)

    def get(self, key):
        hit = self.entries.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        return None

    def put(self, key, value) -> None:
        self.entries[key] = (time.monotonic(), value)


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
        if (hit := self._cache.get(key)) is None:
            hit = self._client.count(query)
            self._cache.put(key, hit)
        return hit

    def _fetch(self, query: StudyQuery, fields: tuple[str, ...], limit: int | None):
        key = ("fetch", tuple(sorted(query.to_params().items())), fields, limit)
        if (hit := self._cache.get(key)) is None:
            hit = self._client.fetch_studies(query, fields=fields, limit=limit)
            self._cache.put(key, hit)
        return hit
