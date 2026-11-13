"""Pipeline: QueryRequest -> QueryResponse. Wires the planner, compiler, client, and builder."""

import logging

from pydantic import ValidationError

from trialviz.builder import build_response
from trialviz.compiler import compile_plan
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.planner import Planner
from trialviz.plan import Analysis, QueryPlan
from trialviz.retrieve import Retriever, TTLCache
from trialviz.schemas import LLMInfo, QueryRequest, QueryResponse

log = logging.getLogger(__name__)

TOTAL_CITATIONS = 25  # a `total` answer uses the registry count; fetch a few to cite

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


class Pipeline:
    def __init__(
        self,
        planner: Planner,
        client: CTGovClient,
        *,
        max_records: int = 20_000,
        sample_records: int = 3000,
        cache_ttl_s: float = 3600,
    ):
        self._planner = planner
        self._client = client
        self._cache = TTLCache(cache_ttl_s)
        self._retriever = Retriever(
            client, max_records=max_records, sample_records=sample_records, cache=self._cache
        )

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
        if plan.analysis is Analysis.TOTAL:
            fetched = [
                (q.series, self._retriever.citations(q.query, compiled.fields, TOTAL_CITATIONS))
                for q in compiled.queries
            ]
        else:
            fetched = [
                (q.series, self._retriever.retrieve(q.query, compiled.fields, compiled.buckets))
                for q in compiled.queries
            ]
        return build_response(plan, fetched, self._data_timestamp(), llm)

    def _data_timestamp(self) -> str | None:
        if (hit := self._cache.get("version")) is not None:
            return hit
        ts = self._client.version().data_timestamp
        self._cache.put("version", ts)
        return ts
