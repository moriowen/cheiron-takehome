import json
from pathlib import Path

import pytest

from tests.test_plan import NO_FILTERS, TAXONOMY, valid
from trialviz.builder import MAX_CITATIONS, build_response, select_viz
from trialviz.ctgov.client import FetchResult
from trialviz.schemas import LLMInfo, NetworkData, QueryResponse, VizType

FIXTURES = Path(__file__).parent / "fixtures"
PEMBRO = json.loads((FIXTURES / "pembrolizumab.json").read_text())["studies"]
MELANOMA = json.loads((FIXTURES / "melanoma.json").read_text())["studies"]
TS = "2026-11-12T10:00:05"
LLM = LLMInfo(model="stub", prompt_version="t", attempts=1, cost_usd=0.0)


def fetched(plan, studies=PEMBRO, total=3006):
    n = len(plan.comparison.values) if plan.comparison else 1
    names = plan.comparison.values if plan.comparison else [None]
    halves = [studies[i::n] for i in range(n)]
    return [(name, FetchResult(h, total, True)) for name, h in zip(names, halves, strict=True)]


EXPECTED_TYPE = {
    "trend": VizType.TIME_SERIES,
    "trend comparison": VizType.TIME_SERIES,
    "distribution": VizType.BAR_CHART,
    "top n": VizType.BAR_CHART,
    "geographic": VizType.BAR_CHART,
    "comparison": VizType.GROUPED_BAR_CHART,
    "cross-tab": VizType.GROUPED_BAR_CHART,
    "bipartite network": VizType.NETWORK_GRAPH,
    "co-occurrence network": VizType.NETWORK_GRAPH,
    "histogram": VizType.HISTOGRAM,
    "scatter": VizType.SCATTER_PLOT,
    "total": VizType.METRIC,
}


@pytest.mark.parametrize("name", list(EXPECTED_TYPE))
def test_every_taxonomy_row_builds_a_valid_response(name):
    plan = valid(**TAXONOMY[name])
    assert select_viz(plan) is EXPECTED_TYPE[name]

    resp = build_response(plan, fetched(plan, MELANOMA if "network" in name else PEMBRO), TS, LLM)
    QueryResponse.model_validate(resp.model_dump())  # round-trips through the public schema
    v = resp.visualization
    assert resp.status == "ok" and v.type is EXPECTED_TYPE[name]
    assert v.title

    data = v.data.nodes + v.data.edges if isinstance(v.data, NetworkData) else v.data
    assert data, "no data"
    for d in data:
        d = d if isinstance(d, dict) else d.model_dump()
        assert d["citation_count"] >= len({c["nct_id"] for c in d["citations"]})
        assert len({c["nct_id"] for c in d["citations"]}) <= MAX_CITATIONS
        if d["citation_count"]:
            assert d["citations"], "a non-empty datum must cite something"

    m = resp.meta
    assert m.records_used > 0 and m.total_matching >= m.records_used
    assert m.truncated and any("first" in n for n in m.notes)
    assert m.plan == plan and m.llm == LLM and m.data_timestamp == TS


def test_bar_counts_match_records():
    plan = valid(group_by=["phase"])
    resp = build_response(plan, fetched(plan), TS, LLM)
    assert sum(r["trial_count"] for r in resp.visualization.data) == len(PEMBRO)
    assert not resp.meta.multi_valued


def test_phase_bars_follow_phase_order():
    plan = valid(group_by=["phase"])
    labels = [r["phase"] for r in build_response(plan, fetched(plan), TS, LLM).visualization.data]
    assert labels.index("Phase 1") < labels.index("Phase 2") < labels.index("Phase 3")


def test_top_n_cuts_and_notes_the_rest():
    plan = valid(group_by=["country"], top_n=5)
    resp = build_response(plan, fetched(plan), TS, LLM)
    assert len(resp.visualization.data) == 5
    assert resp.meta.multi_valued
    assert any("not shown" in n for n in resp.meta.notes)


def test_entity_breakdown_is_horizontal_and_sorted():
    plan = valid(group_by=["lead_sponsor"])
    v = build_response(plan, fetched(plan), TS, LLM).visualization
    assert v.encoding["y"].type == "nominal" and v.encoding["x"].type == "quantitative"
    counts = [r["trial_count"] for r in v.data]
    assert counts == sorted(counts, reverse=True)


def test_trend_fills_gaps_and_marks_estimates():
    plan = valid(analysis="trend", group_by=["start_year"])
    resp = build_response(plan, fetched(plan), TS, LLM)
    years = [int(r["year"]) for r in resp.visualization.data]
    assert years == list(range(min(years), max(years) + 1))
    assert all(r["estimated_count"] <= r["trial_count"] for r in resp.visualization.data)
    assert resp.meta.time_granularity == "year"


def test_comparison_rows_cover_every_series_and_bucket():
    plan = valid(**TAXONOMY["comparison"])
    rows = build_response(plan, fetched(plan), TS, LLM).visualization.data
    series = {r["series"] for r in rows}
    phases = {r["phase"] for r in rows}
    assert series == {"a", "b"}
    assert len(rows) == len(series) * len(phases)  # zeros filled in


def test_metric():
    plan = valid(analysis="total", group_by=[])
    v = build_response(plan, fetched(plan), TS, LLM).visualization
    assert v.data[0]["trial_count"] == 3006  # registry total, not the fetched sample
    assert v.data[0]["citation_count"] == 3006


def test_title_names_the_scope():
    plan = valid(filters=NO_FILTERS | {"drug": "pembrolizumab", "status": ["RECRUITING"]})
    title = build_response(plan, fetched(plan), TS, LLM).visualization.title
    assert "pembrolizumab" in title.lower() and "phase" in title.lower()
    assert "recruiting" in title.lower()


def test_no_matches_is_ok_with_a_note():
    plan = valid()
    resp = build_response(plan, [(None, FetchResult([], 0, False))], TS, LLM)
    assert resp.status == "ok" and resp.visualization.data == []
    assert any("No trials" in n for n in resp.meta.notes)


def test_unsupported_plan():
    plan = valid(
        supported=False, unsupported_reason="Efficacy is not in the registry.", analysis=None
    )
    resp = build_response(plan, [], TS, LLM)
    assert resp.status == "unsupported" and resp.visualization is None
    assert resp.meta.notes == ["Efficacy is not in the registry."]


def test_title_uses_phase_labels_and_starts_capitalized():
    plan = valid(filters=NO_FILTERS | {"drug": "pembrolizumab", "phase": ["PHASE3"]})
    title = build_response(plan, fetched(plan), TS, LLM).visualization.title
    assert title[0].isupper()
    assert "Phase 3" in title and "phase3" not in title.lower()


def test_missing_note_grammar():
    plan = valid(analysis="histogram", group_by=[], measures=["enrollment"])
    one_missing = PEMBRO[:3] + [{"protocolSection": {"identificationModule": {"nctId": "NCTX"}}}]
    notes = build_response(plan, [(None, FetchResult(one_missing, 4, False))], TS, LLM).meta.notes
    assert "1 trial has no enrollment and is not shown." in notes
