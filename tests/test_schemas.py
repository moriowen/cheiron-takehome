import pytest
from pydantic import ValidationError

from trialviz.schemas import (
    Channel,
    Citation,
    Meta,
    NetworkData,
    QueryRequest,
    QueryResponse,
    Visualization,
    VizType,
)

# --- request --------------------------------------------------------------------------------


def test_request_minimal():
    assert QueryRequest(query="q").hints() == {}


def test_request_hints_exclude_unset_and_query():
    r = QueryRequest(query="q", drug_name="Pembrolizumab", phase=["PHASE3"])
    assert r.hints() == {"drug_name": "Pembrolizumab", "phase": ["PHASE3"]}


@pytest.mark.parametrize(
    "bad",
    [
        {"query": ""},
        {"query": "x" * 501},
        {"query": "q", "start_year": 2020, "end_year": 2010},
        {"query": "q", "phase": ["PHASE9"]},
        {"query": "q", "unknown_field": 1},
    ],
)
def test_request_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(bad)


# --- response -------------------------------------------------------------------------------


def meta(**kw) -> Meta:
    return Meta(**{"data_timestamp": "2026-11-12T10:00:05"} | kw)


def bar() -> Visualization:
    return Visualization(
        type=VizType.BAR_CHART,
        title="Trials by phase",
        encoding={
            "x": Channel(field="phase", type="nominal", title="Phase"),
            "y": Channel(field="trial_count", type="quantitative", title="Trials"),
        },
        data=[
            {
                "phase": "Phase 3",
                "trial_count": 1,
                "citation_count": 1,
                "citations": [
                    Citation(
                        nct_id="NCT02142738",
                        field="protocolSection.designModule.phases",
                        excerpt="PHASE3",
                    ).model_dump()
                ],
            }
        ],
    )


def test_ok_response_needs_visualization():
    QueryResponse(status="ok", visualization=bar(), meta=meta())
    with pytest.raises(ValidationError, match="visualization"):
        QueryResponse(status="ok", visualization=None, meta=meta())


def test_unsupported_response_has_no_visualization_and_a_reason():
    QueryResponse(status="unsupported", visualization=None, meta=meta(notes=["Out of scope."]))
    with pytest.raises(ValidationError):
        QueryResponse(status="unsupported", visualization=bar(), meta=meta(notes=["x"]))
    with pytest.raises(ValidationError, match="notes"):
        QueryResponse(status="unsupported", visualization=None, meta=meta())


def test_encoding_fields_must_exist_in_rows():
    v = bar()
    with pytest.raises(ValidationError, match="missing"):
        Visualization(
            type=v.type,
            title=v.title,
            encoding={"x": Channel(field="nope", type="nominal")},
            data=v.data,
        )


def test_network_uses_nodes_and_edges():
    citation = bar().data[0]["citations"][0]
    data = NetworkData(
        nodes=[
            {
                "id": nid,
                "label": nid,
                "group": "drug",
                "weight": 2,
                "citation_count": 2,
                "citations": [citation],
            }
            for nid in ("a", "b")
        ],
        edges=[
            {
                "source": "a",
                "target": "b",
                "weight": 2,
                "citation_count": 2,
                "citations": [citation],
            }
        ],
    )
    from tests.test_builder import fetched
    from tests.test_plan import valid
    from trialviz.builder import build_response

    plan = valid(analysis="network", group_by=["lead_sponsor", "drug"])
    encoding = build_response(plan, fetched(plan), "TS", None).visualization.encoding
    Visualization(type=VizType.NETWORK_GRAPH, title="t", encoding=encoding, data=data)


def test_network_edges_reference_known_nodes():
    with pytest.raises(ValidationError, match="unknown node"):
        NetworkData(
            nodes=[
                {
                    "id": "a",
                    "label": "a",
                    "group": "drug",
                    "weight": 1,
                    "citation_count": 0,
                    "citations": [],
                }
            ],
            edges=[
                {"source": "a", "target": "zzz", "weight": 1, "citation_count": 0, "citations": []}
            ],
        )


def test_json_schema_exports():
    schema = QueryResponse.model_json_schema()
    assert "visualization" in schema["properties"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["encoding"].pop("y"),
        lambda v: v["data"][0].update(trial_count=-1),
        lambda v: v["data"][0].update(trial_count=1.5),
        lambda v: v["data"][0].update(citation_count=2),
        lambda v: v["data"][0].update(citations=[]),
        lambda v: v["data"][0]["citations"][0].update(nct_id="bad"),
        lambda v: v["data"][0]["citations"][0].update(field=""),
        lambda v: v["encoding"]["y"].update(type="nominal"),
        lambda v: v.update(type="histogram"),
    ],
)
def test_invalid_chart_contract_is_rejected(mutation):
    payload = bar().model_dump()
    mutation(payload)
    with pytest.raises(ValidationError):
        Visualization.model_validate(payload)


def test_openapi_exports_typed_rows():
    defs = QueryResponse.model_json_schema()["$defs"]
    assert {"CountRow", "TrendRow", "HistogramRow", "ScatterRow", "MetricRow"} <= defs.keys()
