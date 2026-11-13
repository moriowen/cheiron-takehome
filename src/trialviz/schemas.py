"""Public request and response schemas. The README documents these; keep them in sync."""

import re
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from trialviz.chart_rows import (
    ChartRow,
    CountRow,
    HistogramRow,
    MetricRow,
    ScatterRow,
    TrendRow,
)
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
    nct_id: str = Field(min_length=1, description="ClinicalTrials.gov trial ID.")
    field: str = Field(min_length=1, description="JSON path of the cited value.")
    excerpt: str = Field(min_length=1, description="Exact value of that field.")
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
    weight: int = Field(ge=0, strict=True, description="Trials that include this node.")
    citation_count: int = Field(ge=0, strict=True)
    citations: list[Citation]


class Edge(BaseModel):
    source: str
    target: str
    weight: int = Field(ge=0, strict=True, description="Trials that link source and target.")
    citation_count: int = Field(ge=0, strict=True)
    citations: list[Citation]


class NetworkData(BaseModel):
    nodes: list[Node]
    edges: list[Edge]

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> Self:
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("duplicate network node IDs")
        pairs = set()
        weights = {n.id: n.weight for n in self.nodes}
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"edge {e.source}->{e.target} references an unknown node")
            pair = frozenset((e.source, e.target))
            if e.source == e.target or pair in pairs:
                raise ValueError("duplicate edge or self edge")
            pairs.add(pair)
            if e.weight > min(weights[e.source], weights[e.target]):
                raise ValueError("edge weight exceeds endpoint weight")
        for datum in [*self.nodes, *self.edges]:
            _check_evidence(datum.weight, datum.citation_count, datum.citations)
        return self


class Visualization(BaseModel):
    type: VizType
    title: str
    subtitle: str | None = None
    encoding: dict[str, Channel] = Field(
        description="Channel name -> data field. Channels per type are listed in the README."
    )
    data: list[ChartRow] | NetworkData = Field(
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
        required = {
            VizType.BAR_CHART: {"x", "y"},
            VizType.GROUPED_BAR_CHART: {"x", "y", "series"},
            VizType.TIME_SERIES: {"x", "y"},
            VizType.HISTOGRAM: {"x", "x2", "y"},
            VizType.SCATTER_PLOT: {"x", "y", "tooltip"},
            VizType.NETWORK_GRAPH: {
                "node_id",
                "node_label",
                "node_color",
                "node_size",
                "edge_width",
            },
            VizType.METRIC: {"value", "label"},
        }[self.type]
        if not required <= self.encoding.keys():
            raise ValueError(
                f"missing required channels: {sorted(required - self.encoding.keys())}"
            )
        fixed_fields = {
            VizType.GROUPED_BAR_CHART: {"y": "trial_count", "series": "series"},
            VizType.TIME_SERIES: {"x": "year", "y": "trial_count"},
            VizType.HISTOGRAM: {"x": "bin_start", "x2": "bin_end", "y": "trial_count"},
            VizType.SCATTER_PLOT: {"tooltip": "title"},
            VizType.NETWORK_GRAPH: {
                "node_id": "id",
                "node_label": "label",
                "node_color": "group",
                "node_size": "weight",
                "edge_width": "weight",
            },
            VizType.METRIC: {"value": "trial_count", "label": "label"},
        }.get(self.type, {})
        for name, field in fixed_fields.items():
            if self.encoding[name].field != field:
                raise ValueError(f"channel {name} must reference {field}")
        if self.type is VizType.BAR_CHART:
            if sum(self.encoding[name].field == "trial_count" for name in ("x", "y")) != 1:
                raise ValueError("bar chart needs one trial_count axis")
        for channel in self.encoding.values():
            if channel.field in {
                "trial_count",
                "weight",
                "bin_start",
                "bin_end",
                "enrollment",
                "duration_months",
            }:
                if channel.type != "quantitative":
                    raise ValueError(f"{channel.field} needs a quantitative channel")
        if not is_network:
            row_type = {
                VizType.BAR_CHART: CountRow,
                VizType.GROUPED_BAR_CHART: CountRow,
                VizType.TIME_SERIES: TrendRow,
                VizType.HISTOGRAM: HistogramRow,
                VizType.SCATTER_PLOT: ScatterRow,
                VizType.METRIC: MetricRow,
            }[self.type]
            adapter = TypeAdapter(row_type)
            for row in self.data:
                adapter.validate_python(row)
                count = row.get("trial_count", 1)
                _check_evidence(count, row["citation_count"], row["citations"])
                if "estimated_count" in row and row["estimated_count"] > count:
                    raise ValueError("estimated_count exceeds trial_count")
                if "bin_end" in row and row["bin_end"] is not None:
                    if row["bin_end"] <= row["bin_start"]:
                        raise ValueError("bin_end must exceed bin_start")
                if self.type is VizType.GROUPED_BAR_CHART and "series" not in row:
                    raise ValueError("grouped row needs series")
        return self


def _check_evidence(count, citation_count, citations):
    if citation_count != count:
        raise ValueError("citation_count must equal the datum count")
    ids = {c["nct_id"] if isinstance(c, dict) else c.nct_id for c in citations}
    if any(not re.fullmatch(r"NCT\d{8}", nid) for nid in ids):
        raise ValueError("invalid citation trial ID")
    if len(ids) > min(5, count):
        raise ValueError("citations exceed supporting trials or the five-trial limit")
    if count and not ids:
        raise ValueError("a positive datum needs citations")


class LLMInfo(BaseModel):
    model: str
    prompt_version: str
    attempts: int
    cost_usd: float


CountMethod = Literal["registry_count", "all_records", "sample"]


class SeriesCoverage(BaseModel):
    """How completely one series was counted."""

    series: str | None = Field(description="Compared value, or null without a comparison.")
    total_matching: int = Field(ge=0, strict=True)
    trials_counted: int = Field(ge=0, strict=True)
    count_completeness: Literal["exact", "sample"]
    count_method: CountMethod
    records_fetched: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _coverage_consistent(self) -> Self:
        if self.trials_counted > self.total_matching:
            raise ValueError("trials_counted exceeds total_matching")
        if (self.count_completeness == "exact") != (self.trials_counted == self.total_matching):
            raise ValueError("count completeness disagrees with coverage")
        return self


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
    total_matching: int = Field(default=0, ge=0, description="All registry matches for the query.")
    trials_counted: int = Field(
        default=0, ge=0, description="Trials the counts cover (= total_matching when exact)."
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
        default=0, ge=0, description="Study records downloaded (for counting and for citations)."
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
    ignored_constraints: list[str] = Field(
        default_factory=list,
        description="Conditions from the question that were not applied (no filter exists). "
        "When set, the chart covers a wider set of trials than asked; the subtitle says so.",
    )
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
