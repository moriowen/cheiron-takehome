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
from trialviz.schemas import QueryRequest

FIXTURES = Path(__file__).parent / "fixtures"
PEMBRO = json.loads((FIXTURES / "pembrolizumab.json").read_text())


def fake_ctgov(calls: list | None = None) -> CTGovClient:
    """Serves the pembrolizumab fixture as a single page for any /studies query."""

    def handler(req: httpx.Request):
        if calls is not None:
            calls.append(dict(req.url.params))
        if req.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "TS"})
        return httpx.Response(200, json={"totalCount": 3006, "studies": PEMBRO["studies"]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://ct.test")
    return CTGovClient(http=http, backoff_s=0)


PHASE_Q = "How are pembrolizumab trials distributed across phases?"
PLANS = {
    PHASE_Q: valid(),
    "network?": valid(analysis="network", group_by=["lead_sponsor", "drug"]),
    "efficacy?": valid(supported=False, unsupported_reason="Not in registry.", analysis=None),
}


def pipeline(calls=None) -> Pipeline:
    return Pipeline(StubPlanner(PLANS), fake_ctgov(calls), max_records=500)


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
    p.run(QueryRequest(query=PHASE_Q))
    assert len([c for c in calls if "pageSize" in c]) == 1


def test_fetch_asks_only_for_needed_fields_and_respects_cap():
    calls = []
    pipeline(calls).run(QueryRequest(query=PHASE_Q))
    [fetch] = [c for c in calls if "pageSize" in c]
    assert "designModule.phases" in fetch["fields"] and "locations" not in fetch["fields"]
    assert int(fetch["pageSize"]) <= 500


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
