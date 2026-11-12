"""Planner that returns fixed plans. For tests and offline demos; never calls a model."""

from collections.abc import Mapping

from trialviz.llm.planner import PlannerError, PlanResult
from trialviz.llm.usage import Usage
from trialviz.plan import QueryPlan
from trialviz.schemas import QueryRequest


class StubPlanner:
    def __init__(self, plans: Mapping[str, QueryPlan]):
        self._plans = dict(plans)

    def plan(self, request: QueryRequest) -> PlanResult:
        try:
            plan = self._plans[request.query]
        except KeyError:
            raise PlannerError(f"no stub plan for query {request.query!r}") from None
        return PlanResult(plan, model="stub", prompt_version="stub", attempts=1, usage=Usage(),
                          cost_usd=0.0)  # fmt: skip
