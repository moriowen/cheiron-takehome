import pytest
from pydantic import ValidationError

from trialviz.plan import Dimension, QueryPlan, plan_json_schema


def make(**overrides) -> dict:
    base = {
        "supported": True,
        "unsupported_reason": None,
        "group_by": ["phase"],
        "filters": {
            "drug": "pembrolizumab",
            "condition": None,
            "sponsor": None,
            "country": None,
            "start_year": None,
            "end_year": None,
        },
        "comparison": None,
    }
    base.update(overrides)
    return base


def test_valid_plan_parses():
    plan = QueryPlan.model_validate(make())
    assert plan.group_by == [Dimension.PHASE]
    assert plan.filters.drug == "pembrolizumab"


def test_year_range_must_be_ordered():
    with pytest.raises(ValidationError, match="start_year"):
        QueryPlan.model_validate(
            make(filters=make()["filters"] | {"start_year": 2020, "end_year": 2015})
        )


def test_supported_plan_needs_a_dimension():
    with pytest.raises(ValidationError, match="group_by"):
        QueryPlan.model_validate(make(group_by=[]))


def test_unsupported_plan_needs_a_reason():
    with pytest.raises(ValidationError, match="unsupported_reason"):
        QueryPlan.model_validate(make(supported=False, group_by=[], unsupported_reason=None))


def test_unsupported_plan_with_reason_is_valid():
    plan = QueryPlan.model_validate(
        make(supported=False, group_by=[], unsupported_reason="Efficacy is not in the registry.")
    )
    assert not plan.supported


def test_group_by_has_no_duplicates():
    with pytest.raises(ValidationError, match="duplicate"):
        QueryPlan.model_validate(make(group_by=["phase", "phase"]))


def test_comparison_needs_two_distinct_values():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(make(comparison={"field": "drug", "values": ["a"]}))
    with pytest.raises(ValidationError, match="distinct"):
        QueryPlan.model_validate(make(comparison={"field": "drug", "values": ["A", "a"]}))


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
    schema = plan_json_schema()
    objects = list(_walk_objects(schema))
    assert objects, "schema has no objects"
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
