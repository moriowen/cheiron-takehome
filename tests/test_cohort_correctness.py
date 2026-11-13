"""Six manually labeled records, independent of the real fixture samples.

IDs 1,2,3,5,6 have asthma; ID 4 is a diabetes distractor.
Alpha appears in 1,2,4,6; Beta in 3,5,6. ID 6 has both drugs.
Recruiting: 2,3,4,6. US sites: 1,2,4,6; France: 2,3; Germany: 5.
Phases: 1,5 are phase 2; the rest phase 3. Start years are below.
These labels are hand-written, never derived with the production registry.
"""

import copy
import re

import httpx
import pytest

from evals.e2e_eval import check_execution, check_response
from tests.test_plan import NO_FILTERS, valid
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.stub_planner import StubPlanner
from trialviz.pipeline import Pipeline
from trialviz.schemas import QueryRequest

# id, drugs, condition, phase, status, countries, start year, sponsor
LABELS = [
    (1, ["Alpha"], "Asthma", "PHASE2", "COMPLETED", ["United States"], 2018, "Acme"),
    (2, ["Alpha"], "Asthma", "PHASE3", "RECRUITING", ["United States", "France"], 2020, "Acme"),
    (3, ["Beta"], "Asthma", "PHASE3", "RECRUITING", ["France"], 2021, "Beta Labs"),
    (4, ["Alpha"], "Diabetes", "PHASE3", "RECRUITING", ["United States"], 2021, "Beta Labs"),
    (5, ["Beta"], "Asthma", "PHASE2", "COMPLETED", ["Germany"], 2017, "Beta Labs"),
    (6, ["Alpha", "Beta"], "Asthma", "PHASE3", "RECRUITING", ["United States"], 2022, "Acme"),
]


def record(row):
    i, drugs, condition, phase, status, countries, year, sponsor = row
    return {
        "protocolSection": {
            "identificationModule": {"nctId": f"NCT{i:08}", "briefTitle": f"Labeled trial {i}"},
            "conditionsModule": {"conditions": [condition]},
            "designModule": {"phases": [phase], "enrollmentInfo": {"count": i * 100}},
            "statusModule": {
                "overallStatus": status,
                "startDateStruct": {"date": f"{year}-01-01", "type": "ACTUAL"},
            },
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": "INDUSTRY"}},
            "armsInterventionsModule": {
                "interventions": [{"type": "DRUG", "name": d} for d in drugs]
            },
            "contactsLocationsModule": {"locations": [{"country": c} for c in countries]},
        }
    }


def run(plan, hints=None):
    calls, fetched = [], []

    def handler(req):
        p = dict(req.url.params)
        calls.append(p)
        if req.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "2", "dataTimestamp": "fixture"})
        if req.url.path.endswith("/int/suggest"):
            return httpx.Response(200, json=[])
        selected = []
        # This tiny registry implements query semantics from the labels, without using
        # compiler, extraction, or aggregation helpers from the application.
        for row in LABELS:
            _, drugs, condition, phase, status, countries, year, sponsor = row
            if p.get("query.intr") and p["query.intr"].lower() not in [d.lower() for d in drugs]:
                continue
            if p.get("query.cond") and p["query.cond"].lower() != condition.lower():
                continue
            if p.get("query.spons") and p["query.spons"].lower() != sponsor.lower():
                continue
            if p.get("filter.overallStatus") and status not in p["filter.overallStatus"].split(","):
                continue
            advanced = p.get("filter.advanced", "")
            if match := re.search(r'AREA\[LocationCountry\]"([^"]+)"', advanced):
                if match[1] not in countries:
                    continue
            if match := re.search(r"AREA\[Phase\]\(([^)]+)\)", advanced):
                if phase not in match[1].split(" OR "):
                    continue
            if match := re.search(r"RANGE\[([^,]+), ([^\]]+)\]", advanced):
                lo, hi = match.groups()
                if (lo != "MIN" and year < int(lo[:4])) or (hi != "MAX" and year > int(hi[:4])):
                    continue
            selected.append(record(row))
        size = int(p["pageSize"])
        if size > 1:
            fetched.append(
                {k: v for k, v in p.items() if k not in {"pageSize", "countTotal", "fields"}}
            )
        return httpx.Response(200, json={"studies": selected[:size], "totalCount": len(selected)})

    client = CTGovClient(
        http=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fixture.test"),
        backoff_s=0,
    )
    response = Pipeline(StubPlanner({"fixture": plan}), client).run(
        QueryRequest(query="fixture", **(hints or {}))
    )
    assert check_response(response, {}) == []
    assert check_execution(response, fetched) == []
    return response, calls


def test_filters_select_the_requested_cohort_and_known_count():
    plan = valid(
        filters=NO_FILTERS
        | {
            "drug": "Alpha",
            "condition": "Asthma",
            "country": "United States",
            "status": ["RECRUITING"],
            "phase": ["PHASE3"],
            "start_year": 2020,
            "end_year": 2022,
        }
    )
    response, calls = run(plan)
    assert response.meta.total_matching == 2  # exactly IDs 2 and 6
    assert [(r["phase"], r["trial_count"]) for r in response.visualization.data] == [("Phase 3", 2)]
    assert {c["nct_id"] for c in response.visualization.data[0]["citations"]} == {
        "NCT00000002",
        "NCT00000006",
    }
    assert any("AREA[Phase](PHASE3)" in p.get("filter.advanced", "") for p in calls)


def test_known_comparison_counts_and_overlap():
    response, _ = run(
        valid(
            filters=NO_FILTERS | {"condition": "Asthma"},
            comparison={"field": "drug", "values": ["Alpha", "Beta"]},
        )
    )
    assert {(r["series"], r["phase"]): r["trial_count"] for r in response.visualization.data} == {
        ("Alpha", "Phase 2"): 1,
        ("Alpha", "Phase 3"): 2,
        ("Beta", "Phase 2"): 1,
        ("Beta", "Phase 3"): 2,
    }
    assert [c.total_matching for c in response.meta.coverage] == [3, 3]


def test_known_top_country_with_tie_breaking():
    response, _ = run(
        valid(
            group_by=["country"],
            top_n=1,
            filters=NO_FILTERS | {"condition": "Asthma", "status": ["RECRUITING"]},
        )
    )
    assert [(r["country"], r["trial_count"]) for r in response.visualization.data] == [
        ("France", 2)
    ]


def test_known_years_include_zero_gap_and_hint_override():
    response, _ = run(
        valid(
            analysis="trend", group_by=["start_year"], filters=NO_FILTERS | {"condition": "Asthma"}
        ),
        {"start_year": 2018},
    )
    assert {r["year"]: r["trial_count"] for r in response.visualization.data} == {
        "2018": 1,
        "2019": 0,
        "2020": 1,
        "2021": 1,
        "2022": 1,
    }


def test_known_network_weights_do_not_double_count_combination():
    response, _ = run(
        valid(
            analysis="network",
            group_by=["drug", "drug"],
            filters=NO_FILTERS | {"condition": "Asthma"},
        )
    )
    assert {n.label: n.weight for n in response.visualization.data.nodes} == {"Alpha": 3, "Beta": 3}
    [edge] = response.visualization.data.edges
    assert edge.weight == 1 and {c.nct_id for c in edge.citations} == {"NCT00000006"}


@pytest.mark.parametrize("field, value", [("condition", "Diabetes"), ("top_n", 2)])
def test_eval_rejects_wrong_interpretation_even_with_valid_chart(field, value):
    response, _ = run(
        valid(group_by=["country"], top_n=1, filters=NO_FILTERS | {"condition": "Asthma"})
    )
    expected = {"filters": {"condition": "Asthma"}, "top_n": 1}
    broken = copy.deepcopy(response)
    if field == "top_n":
        broken.meta.plan.top_n = value
    else:
        broken.meta.plan.filters.condition = value
    assert check_response(broken, expected)
