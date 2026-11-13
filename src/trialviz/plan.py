"""QueryPlan: the only thing the LLM produces (D02).

The LLM classifies the question into an `analysis` kind with dimensions, measures, and filters.
Code then fetches data, counts it, picks the chart (D13), and attaches citations (D15).
The validators below are the contract: a plan that passes them is executable.
"""

from enum import StrEnum
from typing import Literal, Self

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field, model_validator


class Analysis(StrEnum):
    BREAKDOWN = "breakdown"  # count trials by 1-2 categorical dimensions
    TREND = "trend"  # count trials per start year
    NETWORK = "network"  # links between two entity dimensions
    HISTOGRAM = "histogram"  # distribution of one numeric measure
    SCATTER = "scatter"  # one point per trial, two numeric measures
    TOTAL = "total"  # one number


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


# Dimensions whose values are named entities; these can be network nodes.
ENTITY_DIMENSIONS = frozenset(
    {Dimension.LEAD_SPONSOR, Dimension.DRUG, Dimension.CONDITION, Dimension.COUNTRY}
)


class Measure(StrEnum):
    ENROLLMENT = "enrollment"
    DURATION_MONTHS = "duration_months"


class Phase(StrEnum):
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"
    NA = "NA"


class Status(StrEnum):
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"


class Filters(BaseModel):
    drug: str | None = Field(description="Drug or intervention name, as the user wrote it.")
    condition: str | None = Field(description="Disease or condition, as the user wrote it.")
    sponsor: str | None = Field(description="Sponsor organization name.")
    country: str | None = Field(description="Country where trial sites are located.")
    phase: list[Phase] | None = Field(description="Only trials in these phases.")
    status: list[Status] | None = Field(description="Only trials with these overall statuses.")
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
    analysis: Analysis | None = Field(description="Kind of answer. Null only when unsupported.")
    group_by: list[Dimension] = Field(max_length=2, description="Dimensions to count by.")
    measures: list[Measure] = Field(max_length=2, description="Numeric measures, if any.")
    filters: Filters
    comparison: Comparison | None = Field(description="Set when the user compares 2-4 entities.")
    top_n: int | None = Field(ge=1, le=50, description="Keep only the N largest groups.")
    ignored_constraints: list[str] = Field(
        max_length=6,
        description="Conditions the question states that no filter can express (e.g. "
        "'randomized', 'adults only', 'with posted results'), in the user's words. The answer "
        "then covers a wider set of trials. Empty when every condition is applied.",
    )
    interpretation_notes: list[str] = Field(
        max_length=4,
        description="Short notes on readings a user might not expect (e.g. 'after 2019' read "
        "as start_year 2020), or requested extras the chart does not show. Empty if none.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.supported:
            if not self.unsupported_reason:
                raise ValueError("an unsupported plan needs an unsupported_reason")
            return self
        if self.analysis is None:
            raise ValueError("a supported plan needs an analysis")
        _check_shape(self)
        if self.comparison:
            if self.analysis not in {Analysis.BREAKDOWN, Analysis.TREND, Analysis.TOTAL}:
                raise ValueError("comparison works only with breakdown, trend, or total")
            field = self.comparison.field
            if getattr(self.filters, field) is not None:
                raise ValueError(f"comparison field {field!r} must not also be set as a filter")
            if _COMPARISON_DIMENSION[field] in self.group_by:
                raise ValueError(
                    f"comparison field {field!r} is the series; remove it from group_by"
                )
            if self.analysis is Analysis.BREAKDOWN and len(self.group_by) != 1:
                raise ValueError("a breakdown with a comparison takes exactly one group_by")
        return self


_COMPARISON_DIMENSION = {
    "drug": Dimension.DRUG,
    "condition": Dimension.CONDITION,
    "sponsor": Dimension.LEAD_SPONSOR,
}


def _check_shape(p: QueryPlan) -> None:
    dims, measures = p.group_by, p.measures
    counting = {Analysis.BREAKDOWN, Analysis.TREND, Analysis.NETWORK, Analysis.TOTAL}
    if p.analysis in counting and measures:
        raise ValueError(f"{p.analysis} counts trials; measures must be empty")
    match p.analysis:
        case Analysis.BREAKDOWN:
            if not 1 <= len(dims) <= 2:
                raise ValueError("breakdown needs 1 or 2 group_by dimensions")
            if len(set(dims)) != len(dims):
                raise ValueError("breakdown dimensions must be distinct")
        case Analysis.TREND:
            if dims != [Dimension.START_YEAR]:
                raise ValueError("trend must group_by exactly [start_year]")
        case Analysis.NETWORK:
            if len(dims) != 2 or not set(dims) <= ENTITY_DIMENSIONS:
                allowed = ", ".join(sorted(ENTITY_DIMENSIONS))
                raise ValueError(f"network needs two entity dimensions from: {allowed}")
        case Analysis.HISTOGRAM:
            if dims or len(measures) != 1:
                raise ValueError("histogram needs exactly one measure and no group_by")
        case Analysis.SCATTER:
            if dims or len(measures) != 2 or measures[0] == measures[1]:
                raise ValueError("scatter needs two distinct measures and no group_by")
        case Analysis.TOTAL:
            if dims:
                raise ValueError("total takes no group_by")


def plan_json_schema() -> dict:
    """JSON schema for OpenAI strict Structured Outputs.

    Uses the SDK's own converter (private module, pinned by tests/test_plan.py) so the schema
    matches what `client.chat.completions.parse` would send.
    """
    return to_strict_json_schema(QueryPlan)
