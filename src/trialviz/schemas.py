"""Public request and response schemas. The README documents these; keep them in sync."""

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trialviz.plan import Phase, QueryPlan, Status

# --- request --------------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """A question plus optional structured hints. Hints are facts and become filters."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500, description="Natural-language question.")
    drug_name: str | None = Field(default=None, max_length=200)
    condition: str | None = Field(default=None, max_length=200)
    sponsor: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    phase: list[Phase] | None = None
    status: list[Status] | None = None
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def _years_ordered(self) -> Self:
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be <= end_year")
        return self

    def hints(self) -> dict[str, Any]:
        """Structured fields the caller set, without the free-text query."""
        return self.model_dump(mode="json", exclude={"query"}, exclude_none=True)


# --- response -------------------------------------------------------------------------------


class VizType(StrEnum):
    BAR_CHART = "bar_chart"
    GROUPED_BAR_CHART = "grouped_bar_chart"
    TIME_SERIES = "time_series"
    HISTOGRAM = "histogram"
    SCATTER_PLOT = "scatter_plot"
    NETWORK_GRAPH = "network_graph"
    METRIC = "metric"


class Citation(BaseModel):
    nct_id: str = Field(description="Trial ID. Link: https://clinicaltrials.gov/study/{nct_id}")
    field: str = Field(description="JSON path of the cited value in the API study record.")
    excerpt: str = Field(description="Exact value of that field in the API response.")


class Channel(BaseModel):
    """Maps a data field to a visual channel (x, y, series, size, ...)."""

    field: str
    type: Literal["nominal", "ordinal", "quantitative", "temporal"]
    title: str | None = None
    sort: Literal["ascending", "descending"] | None = None


class Node(BaseModel):
    id: str
    label: str
    group: str = Field(description="Dimension the node comes from, e.g. 'drug'.")
    weight: int = Field(description="Number of trials that include this node.")
    citation_count: int
    citations: list[Citation]


class Edge(BaseModel):
    source: str
    target: str
    weight: int = Field(description="Number of trials that link source and target.")
    citation_count: int
    citations: list[Citation]


class NetworkData(BaseModel):
    nodes: list[Node]
    edges: list[Edge]

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> Self:
        ids = {n.id for n in self.nodes}
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"edge {e.source}->{e.target} references an unknown node")
        return self


class Visualization(BaseModel):
    type: VizType
    title: str
    subtitle: str | None = None
    encoding: dict[str, Channel] = Field(
        description="Channel name -> data field. Channels per type are listed in the README."
    )
    data: list[dict[str, Any]] | NetworkData = Field(
        description="Rows for charts (each with citations), or nodes and edges for networks."
    )

    @model_validator(mode="after")
    def _data_matches_type(self) -> Self:
        is_network = isinstance(self.data, NetworkData)
        if is_network != (self.type is VizType.NETWORK_GRAPH):
            raise ValueError("network_graph uses nodes/edges data; other types use rows")
        if not is_network:
            for ch_name, ch in self.encoding.items():
                for row in self.data:
                    if ch.field not in row:
                        raise ValueError(f"encoding {ch_name!r}: field {ch.field!r} missing")
        return self


class LLMInfo(BaseModel):
    model: str
    prompt_version: str
    attempts: int
    cost_usd: float


class Meta(BaseModel):
    plan: QueryPlan | None = Field(default=None, description="How the question was interpreted.")
    filters: dict[str, Any] = Field(default_factory=dict, description="Filters applied.")
    api_query: dict[str, str] = Field(
        default_factory=dict, description="Exact ClinicalTrials.gov API parameters used."
    )
    source: str = "clinicaltrials.gov"
    data_timestamp: str | None = Field(default=None, description="Registry snapshot time.")
    records_used: int = Field(default=0, description="Study records the chart is built from.")
    total_matching: int = Field(default=0, description="All registry matches for the query.")
    truncated: bool = Field(default=False, description="True if records_used < total_matching.")
    multi_valued: bool = Field(
        default=False, description="True if one trial can count in several groups."
    )
    units: dict[str, str] = Field(default_factory=dict, description="Field -> unit.")
    sort: str | None = None
    time_granularity: str | None = None
    notes: list[str] = Field(default_factory=list, description="Assumptions and caveats.")
    llm: LLMInfo | None = None


class QueryResponse(BaseModel):
    status: Literal["ok", "unsupported"]
    visualization: Visualization | None
    meta: Meta

    @model_validator(mode="after")
    def _status_consistent(self) -> Self:
        if self.status == "ok" and self.visualization is None:
            raise ValueError("an ok response needs a visualization")
        if self.status == "unsupported":
            if self.visualization is not None:
                raise ValueError("an unsupported response has no visualization")
            if not self.meta.notes:
                raise ValueError("an unsupported response needs a reason in meta.notes")
        return self
