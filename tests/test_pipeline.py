import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_plan import NO_FILTERS, valid
from trialviz.api import create_app
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.stub_planner import StubPlanner
from trialviz.pipeline import Pipeline, PipelineError, apply_hints
from trialviz.plan import Dimension
from trialviz.registry import DIMENSIONS
from trialviz.schemas import QueryRequest

FIXTURES = Path(__file__).parent / "fixtures"
PEMBRO = json.loads((FIXTURES / "pembrolizumab.json").read_text())


def fake_ctgov(calls: list | None = None) -> CTGovClient:
    """A tiny registry over the pembrolizumab fixture (200 records). It pages like the real
    API and understands the phase bucket clauses, so bucket counts are real counts."""
    phase = DIMENSIONS[Dimension.PHASE]
    by_clause = {clause: key for key, clause in phase.buckets}

    def handler(req: httpx.Request):
        params = dict(req.url.params)
        if calls is not None:
            calls.append(params)
        if req.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "TS"})
        if req.url.path.endswith("/int/suggest"):
            return httpx.Response(200, json=["pembrolizumab"])
        studies = PEMBRO["studies"]
        if params.get("query.intr") == "pembrolizimab":  # a misspelling with no matches
            studies = []
        adv = params.get("filter.advanced", "")
        for clause, key in by_clause.items():
            if adv == clause or adv.endswith(f"AND ({clause})"):
                studies = [s for s in studies if phase.extract(s)[0].key == key]
        start, size = int(params.get("pageToken", 0)), int(params.get("pageSize", 1000))
        body = {"studies": studies[start : start + size]}
        if params.get("countTotal") == "true":
            body["totalCount"] = len(studies)
        if start + size < len(studies):
            body["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=body)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://ct.test")
    return CTGovClient(http=http, backoff_s=0)


PHASE_Q = "How are pembrolizumab trials distributed across phases?"
PLANS = {
    PHASE_Q: valid(),
    "network?": valid(analysis="network", group_by=["lead_sponsor", "drug"]),
    "countries?": valid(group_by=["country"]),
    "typo?": valid(filters=NO_FILTERS | {"drug": "pembrolizimab"}),
    "how many?": valid(analysis="total", group_by=[]),
    "compare?": valid(filters=NO_FILTERS, comparison={"field": "drug", "values": ["a", "b"]}),
    "efficacy?": valid(supported=False, unsupported_reason="Not in registry.", analysis=None),
}


def pipeline(calls=None, **kw) -> Pipeline:
    return Pipeline(StubPlanner(PLANS), fake_ctgov(calls), **{"max_records": 500, **kw})


def fetches(calls: list) -> list[dict]:
    """Record fetches, not counts (a count asks for 1 record) or the version call."""
    return [c for c in calls if c.get("pageSize", "1") != "1"]


# --- pipeline -------------------------------------------------------------------------------


def test_pipeline_end_to_end():
    resp = pipeline().run(QueryRequest(query=PHASE_Q))
    assert resp.status == "ok"
    assert resp.visualization.type == "bar_chart"
    assert resp.meta.data_timestamp == "TS"
    assert resp.meta.llm.model == "stub"


def test_unsupported_skips_the_registry():
    calls = []
    resp = pipeline(calls).run(QueryRequest(query="efficacy?"))
    assert resp.status == "unsupported"
    assert not [c for c in calls if "pageSize" in c]


def test_fetch_is_cached():
    calls = []
    p = pipeline(calls)
    p.run(QueryRequest(query=PHASE_Q))
    n = len(calls)
    p.run(QueryRequest(query=PHASE_Q))
    assert len(calls) == n


def test_fetch_asks_only_for_needed_fields():
    calls = []
    pipeline(calls).run(QueryRequest(query=PHASE_Q))
    [fetch] = fetches(calls)
    assert "designModule.phases" in fetch["fields"] and "locations" not in fetch["fields"]


# --- counting methods (D32, D33) ------------------------------------------------------------


def phase_counts(resp) -> dict[str, int]:
    return {row["phase"]: row["trial_count"] for row in resp.visualization.data}


def test_small_query_counts_every_record():
    resp = pipeline().run(QueryRequest(query=PHASE_Q))
    m = resp.meta
    assert m.count_method == "all_records" and m.count_completeness == "exact"
    assert m.trials_counted == m.total_matching == m.records_fetched == 200
    assert sum(phase_counts(resp).values()) == 200  # phase is single-valued


def test_large_query_uses_exact_registry_counts_per_bucket():
    calls = []
    resp = pipeline(calls, max_records=100).run(QueryRequest(query=PHASE_Q))
    m = resp.meta
    assert m.count_method == "registry_count" and m.count_completeness == "exact"
    assert m.trials_counted == m.total_matching == 200 and m.records_fetched < 200
    assert resp.visualization.subtitle == "Exact counts, all 200 trials"
    assert phase_counts(resp) == phase_counts(pipeline().run(QueryRequest(query=PHASE_Q)))
    assert m.bucket_clauses["PHASE2"].startswith("AREA[Phase]PHASE2 AND NOT")
    assert any("Exact registry counts" in n for n in m.notes)
    assert all(int(c["pageSize"]) <= 5 for c in fetches(calls))  # counts + a few to cite
    for row in resp.visualization.data:  # each bar cites trials of that phase only
        assert row["citations"] and row["citation_count"] == row["trial_count"]


def test_large_query_without_buckets_falls_back_to_a_sample():
    resp = pipeline(max_records=100, sample_records=50).run(QueryRequest(query="countries?"))
    m = resp.meta
    assert m.count_method == "sample" and m.count_completeness == "sample"
    assert (m.trials_counted, m.total_matching) == (50, 200)
    assert any("first 50 of 200" in n for n in m.notes)
    assert resp.visualization.subtitle == "Partly sampled, SAMPLE of 50 of 200 trials"


def test_hints_override_plan_filters():
    plan = valid(filters=NO_FILTERS | {"drug": "keytruda"})
    req = QueryRequest(query="q", drug_name="Pembrolizumab", phase=["PHASE3"], start_year=2015)
    out = apply_hints(plan, req)
    assert out.filters.drug == "Pembrolizumab"
    assert [p.value for p in out.filters.phase] == ["PHASE3"]
    assert out.filters.start_year == 2015


def test_hint_does_not_override_a_comparison_field():
    plan = valid(filters=NO_FILTERS, comparison={"field": "drug", "values": ["a", "b"]})
    out = apply_hints(plan, QueryRequest(query="q", drug_name="c"))
    assert out.filters.drug is None and out.comparison.values == ["a", "b"]


def test_conflicting_hint_is_a_pipeline_error():
    plan = valid(filters=NO_FILTERS | {"end_year": 2010})
    with pytest.raises(PipelineError):
        apply_hints(plan, QueryRequest(query="q", start_year=2015))


# --- entity check (D34) --------------------------------------------------------------------


def test_misspelled_drug_is_corrected_with_a_note():
    resp = pipeline().run(QueryRequest(query="typo?"))
    [check] = resp.meta.entities
    assert check.corrected and (check.value, check.used) == ("pembrolizimab", "pembrolizumab")
    assert resp.meta.plan.filters.drug == "pembrolizumab"
    assert resp.meta.api_query["query.intr"] == "pembrolizumab"
    assert resp.meta.notes[0].startswith('"pembrolizimab" matches no trials')
    assert resp.meta.total_matching == 200


def test_entity_counts_are_cached_with_the_fetch():
    calls = []
    p = pipeline(calls)
    p.run(QueryRequest(query=PHASE_Q))
    counts = [c for c in calls if c.get("pageSize") == "1"]
    assert len(counts) == 1  # the entity check and the retriever share one count


# --- HTTP API -------------------------------------------------------------------------------


@pytest.fixture
def http():
    return TestClient(create_app(pipeline()))


def test_post_query(http):
    r = http.post("/query", json={"query": PHASE_Q})
    assert r.status_code == 200
    body = r.json()
    assert body["visualization"]["type"] == "bar_chart"
    assert body["visualization"]["data"][0]["citations"][0]["nct_id"].startswith("NCT")


def test_network_over_http(http):
    body = http.post("/query", json={"query": "network?"}).json()
    assert body["visualization"]["data"]["nodes"] and body["visualization"]["data"]["edges"]


def test_validation_error_is_422(http):
    assert http.post("/query", json={"query": ""}).status_code == 422
    assert http.post("/query", json={"query": "q", "bogus": 1}).status_code == 422


def test_planner_failure_is_502(http):
    r = http.post("/query", json={"query": "no stub for this"})
    assert r.status_code == 502
    assert "planner" in r.json()["detail"].lower()


def test_health(http):
    assert http.get("/health").json() == {"status": "ok"}


def test_budget_exceeded_is_504():
    p = Pipeline(StubPlanner(PLANS), fake_ctgov(), budget_s=1e-9)
    r = TestClient(create_app(p)).post("/query", json={"query": PHASE_Q})
    assert r.status_code == 504 and "budget" in r.json()["detail"]


def test_total_is_exact_even_though_only_a_few_records_are_fetched_to_cite():
    """Regression: a `total` used to say truncated=True because it fetches 25 records."""
    m = pipeline().run(QueryRequest(query="how many?")).meta
    assert m.count_completeness == "exact" and m.trials_counted == m.total_matching == 200
    assert m.records_fetched == 25
    assert not any("first" in n for n in m.notes)


def test_comparison_reports_coverage_per_series():
    resp = pipeline().run(QueryRequest(query="compare?"))
    cov = resp.meta.coverage
    assert [c.series for c in cov] == ["a", "b"]
    assert all(c.count_completeness == "exact" for c in cov)
    assert resp.visualization.subtitle == "Exact counts, a: all 200 trials; b: all 200 trials"


def test_ignored_comparison_hint_is_disclosed():
    response = pipeline().run(QueryRequest(query="compare?", drug_name="c"))
    assert any("drug_name='c' was not applied" in note for note in response.meta.notes)
