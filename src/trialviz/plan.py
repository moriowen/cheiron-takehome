"""QueryPlan: the only thing the LLM produces.

The LLM maps a question to this typed plan. Code (not the LLM) then fetches data, counts it,
picks the chart, and attaches citations. Keeping the LLM output this small is the main guard
against hallucinated numbers.

Provisional: dimensions and filters will grow with the question taxonomy.
"""

from enum import StrEnum
from typing import Literal, Self

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field, model_validator


class Dimension(StrEnum):
    PHASE = "phase"
    STATUS = "overall_status"
    STUDY_TYPE = "study_type"
    START_YEAR = "start_year"
    LEAD_SPONSOR = "lead_sponsor"
    SPONSOR_CLASS = "sponsor_class"
    INTERVENTION_TYPE = "intervention_type"
    DRUG = "drug"
    CONDITION = "condition"
    COUNTRY = "country"


class Filters(BaseModel):
    drug: str | None = Field(description="Drug or intervention name, as the user wrote it.")
    condition: str | None = Field(description="Disease or condition.")
    sponsor: str | None = Field(description="Sponsor organization name.")
    country: str | None = Field(description="Country where trial sites are located.")
    start_year: int | None = Field(description="Earliest trial start year, inclusive.")
    end_year: int | None = Field(description="Latest trial start year, inclusive.")

    @model_validator(mode="after")
    def _years_ordered(self) -> Self:
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be <= end_year")
        return self


class Comparison(BaseModel):
    field: Literal["drug", "condition", "sponsor"]
    values: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len({v.strip().lower() for v in self.values}) != len(self.values):
            raise ValueError("comparison values must be distinct")
        return self


class QueryPlan(BaseModel):
    supported: bool = Field(description="False if registry data cannot answer the question.")
    unsupported_reason: str | None = Field(description="Why not, when supported is false.")
    group_by: list[Dimension] = Field(
        max_length=2,
        description="One dimension for a distribution or trend; two for a network or cross-tab.",
    )
    filters: Filters
    comparison: Comparison | None = Field(description="Set when the user compares 2-4 entities.")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.supported and not self.group_by:
            raise ValueError("a supported plan needs at least one group_by dimension")
        if not self.supported and not self.unsupported_reason:
            raise ValueError("an unsupported plan needs an unsupported_reason")
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by has duplicate dimensions")
        return self


def plan_json_schema() -> dict:
    """JSON schema for OpenAI strict Structured Outputs.

    Uses the SDK's own converter (private module, pinned by tests/test_plan.py) so the schema
    matches what `client.chat.completions.parse` would send.
    """
    return to_strict_json_schema(QueryPlan)
