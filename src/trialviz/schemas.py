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
    title: str | None = Field(default=None, description="Trial brief title, for display.")
    match: Literal["exact", "alias", "broader", "free_text"] | None = Field(
        default=None,
        description="Drugs and conditions only: how the excerpt maps to the datum's label. "
        "exact: same words as the MeSH term; alias: a name the sponsor gave as the same thing "
        "(otherNames, parentheses, list item, reordered words); broader: a condition grouped "
        "under a MeSH term whose words all appear in it; free_text: no MeSH match, the "
        "cleaned excerpt is the label.",
    )


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


CountMethod = Literal["registry_count", "all_records", "sample"]


class SeriesCoverage(BaseModel):
    """How completely one series was counted."""

    series: str | None = Field(description="Compared value, or null without a comparison.")
    total_matching: int
    trials_counted: int
    count_completeness: Literal["exact", "sample"]
    count_method: CountMethod
    records_fetched: int


class EntityCheck(BaseModel):
    """How one name from the question was checked against the registry (D34)."""

    field: Literal["drug", "condition", "sponsor"]
    value: str = Field(description="The name as the question (or a hint) gave it.")
    matches: int = Field(description="Registry trials matching `value`.")
    used: str = Field(description="The name searched: `value`, or the registry's suggestion.")
    used_matches: int
    corrected: bool = False
    note: str | None = None


class Meta(BaseModel):
    plan: QueryPlan | None = Field(default=None, description="How the question was interpreted.")
    filters: dict[str, Any] = Field(default_factory=dict, description="Filters applied.")
    api_query: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact ClinicalTrials.gov API parameters used. With a comparison: one "
        "parameter set per series, keyed by series name.",
    )
    source: str = "clinicaltrials.gov"
    data_timestamp: str | None = Field(default=None, description="Registry snapshot time.")
    total_matching: int = Field(default=0, description="All registry matches for the query.")
    trials_counted: int = Field(
        default=0, description="Trials the counts cover (= total_matching when exact)."
    )
    count_completeness: Literal["exact", "sample"] | None = Field(
        default=None,
        description="exact: counts cover every matching trial; sample: only trials_counted.",
    )
    count_method: CountMethod | None = Field(
        default=None,
        description="registry_count: exact count queries per bucket; all_records: counted "
        "from every matching record; sample: counted from the first records only.",
    )
    records_fetched: int = Field(
        default=0, description="Study records downloaded (for counting and for citations)."
    )
    citation_limit: int = Field(
        default=5,
        description="Most trials cited per datum. A datum's citation_count is the number of "
        "trials supporting it; `citations` lists up to this many of them.",
    )
    coverage: list[SeriesCoverage] = Field(
        default_factory=list, description="Per series (one entry without a comparison)."
    )
    bucket_clauses: dict[str, str] = Field(
        default_factory=dict,
        description="With registry_count: bucket key -> Essie clause added to api_query "
        "(filter.advanced) to count that bucket.",
    )
    multi_valued: bool = Field(
        default=False, description="True if one trial can count in several groups."
    )
    units: dict[str, str] = Field(default_factory=dict, description="Field -> unit.")
    derivations: dict[str, str] = Field(
        default_factory=dict,
        description="Measure -> how it is computed from the cited fields (derived values).",
    )
    sort: str | None = None
    time_granularity: str | None = None
    entities: list[EntityCheck] = Field(
        default_factory=list, description="Each name in the plan, counted; spelling fixes."
    )
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
