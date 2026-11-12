"""Pipeline: QueryRequest -> QueryResponse. Wires the planner, compiler, client, and builder."""

import logging
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from trialviz.builder import build_response
from trialviz.compiler import compile_plan
from trialviz.ctgov.client import CTGovClient, FetchResult, StudyQuery
from trialviz.llm.planner import Planner
from trialviz.plan import Analysis, QueryPlan
from trialviz.schemas import LLMInfo, QueryRequest, QueryResponse

log = logging.getLogger(__name__)

TOTAL_CITATION_RECORDS = 25  # a `total` answer uses the registry count; fetch a few to cite

# QueryRequest hint -> Filters field
_HINTS = {
    "drug_name": "drug",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "phase": "phase",
    "status": "status",
    "start_year": "start_year",
    "end_year": "end_year",
}


class PipelineError(Exception):
    pass


def apply_hints(plan: QueryPlan, request: QueryRequest) -> QueryPlan:
    """Structured hints are facts from the caller, so they override what the LLM extracted.

    A hint for the field being compared is skipped: the comparison already names the values.
    """
    if not plan.supported:
        return plan
    data = plan.model_dump(mode="json")
    compared = plan.comparison.field if plan.comparison else None
    for hint, filter_field in _HINTS.items():
        value = getattr(request, hint)
        if value is not None and filter_field != compared:
            data["filters"][filter_field] = (
                [v.value for v in value] if isinstance(value, list) else value
            )
    try:
        return QueryPlan.model_validate(data)
    except ValidationError as e:
        raise PipelineError(f"structured hints conflict with the question: {e}") from e


@dataclass
class _Cache:
    ttl_s: float
    entries: dict = field(default_factory=dict)

    def get(self, key):
        hit = self.entries.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        return None

    def put(self, key, value) -> None:
        self.entries[key] = (time.monotonic(), value)


class Pipeline:
    def __init__(
        self,
        planner: Planner,
        client: CTGovClient,
        *,
        max_records: int = 3000,
        cache_ttl_s: float = 3600,
    ):
        self._planner = planner
        self._client = client
        self._max_records = max_records
        self._cache = _Cache(cache_ttl_s)

    def run(self, request: QueryRequest) -> QueryResponse:
        result = self._planner.plan(request)  # PlannerError propagates
        plan = apply_hints(result.plan, request)
        llm = LLMInfo(
            model=result.model,
            prompt_version=result.prompt_version,
            attempts=result.attempts,
            cost_usd=round(result.cost_usd, 6),
        )
        if not plan.supported:
            return build_response(plan, [], self._data_timestamp(), llm)

        compiled = compile_plan(plan)
        limit = TOTAL_CITATION_RECORDS if plan.analysis is Analysis.TOTAL else self._max_records
        fetched = [
            (q.series, self._fetch(q.query, compiled.fields, limit)) for q in compiled.queries
        ]
        return build_response(plan, fetched, self._data_timestamp(), llm)

    def _fetch(self, query: StudyQuery, fields: tuple[str, ...], limit: int) -> FetchResult:
        key = ("fetch", tuple(sorted(query.to_params().items())), fields, limit)
        if (hit := self._cache.get(key)) is not None:
            return hit
        started = time.perf_counter()
        result = self._client.fetch_studies(query, fields=fields, limit=limit)
        log.info(
            "fetched %d of %d studies in %.1fs: %s",
            len(result.studies),
            result.total_count,
            time.perf_counter() - started,
            query.to_params(),
        )
        self._cache.put(key, result)
        return result

    def _data_timestamp(self) -> str | None:
        if (hit := self._cache.get("version")) is not None:
            return hit
        ts = self._client.version().data_timestamp
        self._cache.put("version", ts)
        return ts
