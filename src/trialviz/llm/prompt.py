"""Planner prompt. Bump PROMPT_VERSION on every change so eval results stay comparable."""

import json

from trialviz.plan import Dimension
from trialviz.schemas import QueryRequest

PROMPT_VERSION = "2026-11-12.1"

_DIMENSIONS = ", ".join(d.value for d in Dimension)

SYSTEM_PROMPT = f"""\
You convert a question about clinical trials into a query plan for the ClinicalTrials.gov registry.
You do not answer the question. You do not produce numbers. Code runs the plan.

Rules:
- group_by: the dimension the answer is broken down by. Allowed: {_DIMENSIONS}.
  Use one dimension for a distribution or a trend over time (start_year).
  Use two dimensions for a relationship or network (for example lead_sponsor and drug).
- filters: copy entity names as the user wrote them. Do not correct, expand, or invent names.
  Leave a filter null when the question does not state it.
- comparison: set only when the user compares 2-4 named drugs, conditions, or sponsors.
  Do not also put those names in filters.
- Structured hints, when given, are facts from the user. Use them as filters.
- If registry data cannot answer the question (efficacy, safety outcomes, prices, approvals,
  anything outside trial records), set supported=false and give a one-sentence reason.
"""


def build_messages(request: QueryRequest) -> list[dict[str, str]]:
    user = f"Question:\n<question>\n{request.query}\n</question>"
    if hints := request.hints():
        user += f"\n\nStructured hints:\n{json.dumps(hints)}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def repair_message(errors: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": f"That plan failed validation:\n{errors}\nReturn a corrected plan.",
    }
