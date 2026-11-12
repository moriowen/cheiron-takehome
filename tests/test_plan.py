import pytest
from pydantic import ValidationError

from trialviz.plan import Analysis, Dimension, Measure, QueryPlan, plan_json_schema

NO_FILTERS = {
    "drug": None,
    "condition": None,
    "sponsor": None,
    "country": None,
    "phase": None,
    "status": None,
    "start_year": None,
    "end_year": None,
}


def make(**overrides) -> dict:
    """A valid plan dict (breakdown by phase for pembrolizumab), with overrides."""
    base = {
        "supported": True,
        "unsupported_reason": None,
        "analysis": "breakdown",
        "group_by": ["phase"],
        "measures": [],
        "filters": NO_FILTERS | {"drug": "pembrolizumab"},
        "comparison": None,
        "top_n": None,
    }
    base.update(overrides)
    return base


def valid(**overrides) -> QueryPlan:
    return QueryPlan.model_validate(make(**overrides))


def invalid(match: str, **overrides) -> None:
    with pytest.raises(ValidationError, match=match):
        QueryPlan.model_validate(make(**overrides))


# --- every taxonomy row in docs/02-implementation-plan.md is expressible --------------------

TAXONOMY = {
    "trend": dict(analysis="trend", group_by=["start_year"]),
    "trend comparison": dict(
        analysis="trend",
        group_by=["start_year"],
        filters=NO_FILTERS,
        comparison={"field": "drug", "values": ["nivolumab", "pembrolizumab"]},
    ),
    "distribution": dict(analysis="breakdown", group_by=["phase"]),
    "top n": dict(analysis="breakdown", group_by=["intervention_type"], top_n=10),
    "geographic": dict(
        analysis="breakdown",
        group_by=["country"],
        top_n=15,
        filters=NO_FILTERS | {"condition": "diabetes", "status": ["RECRUITING"]},
    ),
    "comparison": dict(
        analysis="breakdown",
        group_by=["phase"],
        filters=NO_FILTERS,
        comparison={"field": "drug", "values": ["a", "b"]},
    ),
    "cross-tab": dict(analysis="breakdown", group_by=["phase", "overall_status"]),
    "bipartite network": dict(analysis="network", group_by=["lead_sponsor", "drug"]),
    "co-occurrence network": dict(analysis="network", group_by=["drug", "drug"]),
    "histogram": dict(analysis="histogram", group_by=[], measures=["enrollment"]),
    "scatter": dict(analysis="scatter", group_by=[], measures=["enrollment", "duration_months"]),
    "total": dict(analysis="total", group_by=[]),
    "out of scope": dict(
        supported=False, unsupported_reason="Efficacy is not in the registry.", analysis=None
    ),
}


@pytest.mark.parametrize("name", TAXONOMY)
def test_taxonomy_row_is_a_valid_plan(name):
    valid(**TAXONOMY[name])


# --- rules per analysis kind ----------------------------------------------------------------


def test_supported_plan_needs_analysis():
    invalid("analysis", analysis=None)


def test_unsupported_plan_needs_a_reason():
    invalid("unsupported_reason", supported=False, unsupported_reason=None)


def test_trend_groups_by_start_year_only():
    invalid("trend", analysis="trend", group_by=["phase"])


def test_breakdown_needs_one_or_two_dimensions():
    invalid("breakdown", analysis="breakdown", group_by=[])


def test_breakdown_dimensions_are_distinct():
    invalid("distinct", analysis="breakdown", group_by=["phase", "phase"])


def test_network_needs_two_entity_dimensions():
    invalid("network", analysis="network", group_by=["drug"])
    invalid("network", analysis="network", group_by=["phase", "drug"])


def test_histogram_needs_exactly_one_measure_and_no_dimensions():
    invalid("histogram", analysis="histogram", group_by=[], measures=[])
    invalid("histogram", analysis="histogram", group_by=["phase"], measures=["enrollment"])


def test_scatter_needs_two_distinct_measures():
    invalid("scatter", analysis="scatter", group_by=[], measures=["enrollment"])
    invalid("scatter", analysis="scatter", group_by=[], measures=["enrollment", "enrollment"])


def test_count_analyses_take_no_measures():
    invalid("measures", analysis="breakdown", measures=["enrollment"])


def test_total_has_no_dimensions():
    invalid("total", analysis="total", group_by=["phase"])


def test_comparison_only_with_breakdown_trend_total():
    invalid(
        "comparison",
        analysis="network",
        group_by=["lead_sponsor", "drug"],
        filters=NO_FILTERS,
        comparison={"field": "drug", "values": ["a", "b"]},
    )


def test_comparison_field_is_not_also_a_filter():
    invalid("filter", comparison={"field": "drug", "values": ["a", "b"]})


def test_comparison_needs_two_distinct_values():
    invalid("at least 2", filters=NO_FILTERS, comparison={"field": "drug", "values": ["a"]})
    invalid("distinct", filters=NO_FILTERS, comparison={"field": "drug", "values": ["A", "a"]})


def test_year_range_must_be_ordered():
    invalid("start_year", filters=NO_FILTERS | {"start_year": 2020, "end_year": 2015})


def test_top_n_is_bounded():
    invalid("top_n", top_n=0)
    invalid("top_n", top_n=500)


def test_enums_parse():
    plan = valid(filters=NO_FILTERS | {"phase": ["PHASE3"], "status": ["RECRUITING"]})
    assert plan.group_by == [Dimension.PHASE]
    assert plan.analysis is Analysis.BREAKDOWN
    assert Measure("enrollment") is Measure.ENROLLMENT


# --- OpenAI strict mode compatibility -------------------------------------------------------


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


def test_schema_is_strict_mode_compatible():
    """OpenAI strict mode needs every object closed and every property required."""
    objects = list(_walk_objects(plan_json_schema()))
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_comparison_field_is_not_also_a_dimension():
    invalid(
        "group_by",
        analysis="breakdown",
        group_by=["sponsor_class", "condition"],
        filters=NO_FILTERS,
        comparison={"field": "condition", "values": ["a", "b"]},
    )
    invalid(
        "group_by",
        analysis="breakdown",
        group_by=["lead_sponsor"],
        filters=NO_FILTERS,
        comparison={"field": "sponsor", "values": ["a", "b"]},
    )


def test_comparison_breakdown_has_one_dimension():
    invalid(
        "one group_by",
        analysis="breakdown",
        group_by=["phase", "overall_status"],
        filters=NO_FILTERS,
        comparison={"field": "drug", "values": ["a", "b"]},
    )
