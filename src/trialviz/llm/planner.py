"""Planner interface. The rest of the system depends on this, never on a concrete LLM."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trialviz.llm.usage import Usage
from trialviz.plan import QueryPlan
from trialviz.schemas import QueryRequest


@dataclass(frozen=True)
class PlanResult:
    plan: QueryPlan
    model: str
    prompt_version: str
    attempts: int
    usage: Usage
    cost_usd: float


class PlannerError(Exception):
    def __init__(self, message: str, *, attempts: int = 0):
        super().__init__(message)
        self.attempts = attempts


@runtime_checkable
class Planner(Protocol):
    def plan(self, request: QueryRequest) -> PlanResult: ...
