"""Real OpenAI calls. Skipped by default; run with `uv run pytest -m live`. Costs < $0.01."""

import pytest

from trialviz.config import LLMSettings
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.plan import Dimension
from trialviz.schemas import QueryRequest

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def planner():
    return OpenAIPlanner(LLMSettings())


def test_phase_distribution(planner):
    r = planner.plan(QueryRequest(query="How are pembrolizumab trials distributed across phases?"))
    assert r.plan.supported
    assert r.plan.group_by == [Dimension.PHASE]
    assert r.plan.filters.drug.lower() == "pembrolizumab"
    assert r.cost_usd < 0.01


def test_structured_hint_becomes_filter(planner):
    r = planner.plan(
        QueryRequest(query="How many trials started each year since 2015?", condition="Alzheimer")
    )
    assert r.plan.group_by == [Dimension.START_YEAR]
    assert "alzheimer" in r.plan.filters.condition.lower()
    assert r.plan.filters.start_year == 2015


def test_comparison(planner):
    r = planner.plan(QueryRequest(query="Compare phases for trials of nivolumab vs pembrolizumab"))
    assert r.plan.comparison is not None
    assert {v.lower() for v in r.plan.comparison.values} == {"nivolumab", "pembrolizumab"}


def test_out_of_scope(planner):
    r = planner.plan(QueryRequest(query="Is pembrolizumab more effective than chemotherapy?"))
    assert not r.plan.supported
    assert r.plan.unsupported_reason
