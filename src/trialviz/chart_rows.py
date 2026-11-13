"""Typed JSON rows. TypedDict preserves the public dictionary interface and exports schemas."""

from typing import Annotated, Literal, NotRequired

from pydantic import ConfigDict, Field
from typing_extensions import TypedDict

Count = Annotated[int, Field(strict=True, ge=0)]
Number = Annotated[float, Field(ge=0, allow_inf_nan=False)]
TrialID = Annotated[str, Field(pattern=r"^NCT\d{8}$")]


class CitationRow(TypedDict):
    nct_id: TrialID
    field: Annotated[str, Field(min_length=1)]
    excerpt: Annotated[str, Field(min_length=1)]
    title: NotRequired[str | None]
    match: NotRequired[Literal["exact", "alias", "broader", "free_text"] | None]


class EvidenceRow(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    citation_count: Count
    citations: list[CitationRow]


class CountRow(EvidenceRow):
    trial_count: Count
    phase: NotRequired[str]
    overall_status: NotRequired[str]
    study_type: NotRequired[str]
    lead_sponsor: NotRequired[str]
    sponsor_class: NotRequired[str]
    intervention_type: NotRequired[str]
    drug: NotRequired[str]
    condition: NotRequired[str]
    country: NotRequired[str]
    start_year: NotRequired[str]
    series: NotRequired[str]


class TrendRow(EvidenceRow):
    trial_count: Count
    year: Annotated[str, Field(pattern=r"^\d{4}$")]
    estimated_count: Count
    series: NotRequired[str]


class HistogramRow(EvidenceRow):
    trial_count: Count
    bin_start: Number
    bin_end: Number | None


class ScatterRow(EvidenceRow):
    nct_id: TrialID
    title: str | None
    enrollment: Number
    duration_months: Number


class MetricRow(EvidenceRow):
    trial_count: Count
    label: str


ChartRow = CountRow | TrendRow | HistogramRow | ScatterRow | MetricRow
