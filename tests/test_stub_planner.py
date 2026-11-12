import pytest

from tests.test_plan import make
from trialviz.llm.planner import Planner, PlannerError
from trialviz.llm.stub_planner import StubPlanner
from trialviz.plan import QueryPlan
from trialviz.schemas import QueryRequest


def test_returns_canned_plan_for_known_query():
    plan = QueryPlan.model_validate(make())
    stub = StubPlanner({"q1": plan})
    result = stub.plan(QueryRequest(query="q1"))
    assert result.plan == plan
    assert result.cost_usd == 0


def test_unknown_query_fails():
    with pytest.raises(PlannerError):
        StubPlanner({}).plan(QueryRequest(query="nope"))


def test_satisfies_planner_protocol():
    assert isinstance(StubPlanner({}), Planner)
