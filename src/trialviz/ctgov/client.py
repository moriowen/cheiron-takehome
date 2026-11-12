"""ClinicalTrials.gov API v2 client.

Thin and generic: it knows the API's query parameters, paging, and error shape, and nothing
about plans, charts, or citations. API facts this code relies on (probed 2026-11-12):
- `pageSize` max is 1000; `pageSize=0` is ignored, so a count asks for 1 slim record.
- `totalCount` comes back only on the first page, and only with `countTotal=true`.
- The last page has no `nextPageToken`.
- Errors are text/plain: 400 for a bad parameter, 404 for an unknown NCT ID.
- The rate limit is undocumented (not in the OpenAPI spec). A burst of calls got HTTP 429,
  which cleared within seconds. Hence the min interval, backoff, and Retry-After support.
"""

import logging
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2"
MAX_PAGE_SIZE = 1000
_NCT_ID = re.compile(r"^NCT\d{8}$")
_RETRY_STATUS = {429, 500, 502, 503, 504}


class CTGovError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class StudyQuery:
    """Search criteria for `/studies`. Each field maps to one API parameter."""

    condition: str | None = None  # query.cond (synonyms + MeSH)
    intervention: str | None = None  # query.intr (synonyms + MeSH)
    sponsor: str | None = None  # query.spons
    location: str | None = None  # query.locn
    term: str | None = None  # query.term (broad, includes summary text)
    overall_status: Sequence[str] = ()  # filter.overallStatus
    advanced: str | None = None  # filter.advanced (Essie: AREA[Field]value, RANGE[a,b])

    def to_params(self) -> dict[str, str]:
        pairs = {
            "query.cond": self.condition,
            "query.intr": self.intervention,
            "query.spons": self.sponsor,
            "query.locn": self.location,
            "query.term": self.term,
            "filter.overallStatus": ",".join(self.overall_status) or None,
            "filter.advanced": self.advanced,
        }
        return {k: v for k, v in pairs.items() if v}

    def and_advanced(self, clause: str) -> "StudyQuery":
        combined = f"({self.advanced}) AND ({clause})" if self.advanced else clause
        return replace(self, advanced=combined)


@dataclass(frozen=True)
class FetchResult:
    studies: list[dict]
    total_count: int  # all matches in the registry, not only the fetched ones
    truncated: bool  # True when `limit` stopped the fetch before the last page


@dataclass(frozen=True)
class DataVersion:
    api_version: str
    data_timestamp: str


class CTGovClient:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 4,
        backoff_s: float = 1.0,
        min_interval_s: float = 0.0,
    ):
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=timeout_s)
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._min_interval_s = min_interval_s
        self._last_request_at: float | None = None

    def count(self, query: StudyQuery) -> int:
        params = {**query.to_params(), "countTotal": "true", "pageSize": "1", "fields": "NCTId"}
        return self._get("/studies", params)["totalCount"]

    def iter_pages(
        self, query: StudyQuery, *, fields: Sequence[str], page_size: int = MAX_PAGE_SIZE
    ) -> Iterator[dict]:
        """Yields raw page bodies. The first page includes `totalCount`."""
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be 1..{MAX_PAGE_SIZE}")
        params = {
            **query.to_params(),
            "fields": ",".join(fields),
            "pageSize": str(page_size),
            "countTotal": "true",
        }
        while True:
            page = self._get("/studies", params)
            yield page
            token = page.get("nextPageToken")
            if not token:
                return
            params = {**params, "pageToken": token}
            params.pop("countTotal", None)  # only the first page returns it

    def fetch_studies(
        self,
        query: StudyQuery,
        *,
        fields: Sequence[str],
        limit: int | None = None,
        page_size: int = MAX_PAGE_SIZE,
    ) -> FetchResult:
        """Fetches matching studies (only `fields`), up to `limit` records."""
        if limit is not None:
            page_size = min(page_size, limit)
        studies: list[dict] = []
        total = 0
        for i, page in enumerate(self.iter_pages(query, fields=fields, page_size=page_size)):
            if i == 0:
                total = page.get("totalCount", 0)
            studies.extend(page.get("studies", []))
            if limit is not None and len(studies) >= limit:
                studies = studies[:limit]
                break
        return FetchResult(studies, total_count=total, truncated=len(studies) < total)

    def get_study(self, nct_id: str, *, fields: Sequence[str] = ()) -> dict:
        if not _NCT_ID.match(nct_id):
            raise ValueError(f"not an NCT ID: {nct_id!r} (expected NCT + 8 digits)")
        params = {"fields": ",".join(fields)} if fields else {}
        return self._get(f"/studies/{nct_id}", params)

    def version(self) -> DataVersion:
        body = self._get("/version", {})
        return DataVersion(body["apiVersion"], body["dataTimestamp"])

    def _get(self, path: str, params: dict[str, str]) -> dict:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                if last:
                    raise CTGovError(f"ClinicalTrials.gov unreachable: {e}") from e
                self._sleep(attempt, f"transport error: {e}")
                continue
            if resp.status_code in _RETRY_STATUS and not last:
                self._sleep(attempt, f"HTTP {resp.status_code}", _retry_after(resp))
                continue
            if resp.is_error:
                raise CTGovError(
                    f"ClinicalTrials.gov HTTP {resp.status_code}: {resp.text.strip()[:300]}",
                    status_code=resp.status_code,
                )
            return resp.json()
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            wait = self._min_interval_s - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _sleep(self, attempt: int, reason: str, retry_after: float | None = None) -> None:
        delay = retry_after if retry_after is not None else self._backoff_s * 2**attempt
        log.warning("ctgov retry %d after %s; sleeping %.1fs", attempt + 1, reason, delay)
        time.sleep(delay)


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None  # missing, or an HTTP-date we do not parse
