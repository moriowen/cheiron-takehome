"""Chart selector and spec builders: plan + fetched records -> QueryResponse (D12-D15).

`select_viz` is the only place that maps a plan shape to a chart type. Builders turn aggregate
tables into rows, write the encoding, title, and meta notes, and cap citations per datum.
"""

from collections.abc import Sequence
from typing import Any

from trialviz import aggregate as agg
from trialviz.compiler import compile_plan
from trialviz.plan import ENTITY_DIMENSIONS, Analysis, Dimension, QueryPlan
from trialviz.registry import DIMENSIONS, MEASURES, PHASE_LABELS, DimensionSpec
from trialviz.retrieve import CountMethod, Retrieved
from trialviz.schemas import (
    Channel,
    Citation,
    Edge,
    EntityCheck,
    LLMInfo,
    Meta,
    NetworkData,
    Node,
    QueryResponse,
    SeriesCoverage,
    Visualization,
    VizType,
)

MAX_CITATIONS = 5  # trials cited per datum; citation_count has the full number
DEFAULT_TOP_N = 15  # cap for entity breakdowns when the user did not ask for a number
MAX_SERIES_GROUPS = 8  # cap for the second dimension of a cross-tab

Fetched = Sequence[tuple[str | None, Retrieved]]
_WEAKEST_FIRST: tuple[CountMethod, ...] = ("sample", "all_records", "registry_count")


def select_viz(plan: QueryPlan) -> VizType:
    match plan.analysis:
        case Analysis.TREND:
            return VizType.TIME_SERIES
        case Analysis.NETWORK:
            return VizType.NETWORK_GRAPH
        case Analysis.HISTOGRAM:
            return VizType.HISTOGRAM
        case Analysis.SCATTER:
            return VizType.SCATTER_PLOT
        case Analysis.TOTAL:
            return VizType.BAR_CHART if plan.comparison else VizType.METRIC
        case Analysis.BREAKDOWN:
            if plan.comparison or len(plan.group_by) == 2:
                return VizType.GROUPED_BAR_CHART
            return VizType.BAR_CHART
    raise ValueError(f"no visualization for analysis {plan.analysis}")


def build_response(
    plan: QueryPlan,
    fetched: Fetched,
    data_timestamp: str | None,
    llm: LLMInfo | None,
    entities: Sequence[EntityCheck] = (),
) -> QueryResponse:
    if not plan.supported:
        meta = Meta(
            plan=plan,
            data_timestamp=data_timestamp,
            llm=llm,
            notes=[plan.unsupported_reason or "Not answerable from registry data."],
        )
        return QueryResponse(status="unsupported", visualization=None, meta=meta)

    notes: list[str] = [e.note for e in entities if e.note]  # spelling fixes come first
    viz_type = select_viz(plan)
    meta = _base_meta(plan, fetched, data_timestamp, llm, notes)
    builder = _BUILDERS[viz_type]
    encoding, data, extra = builder(plan, fetched, notes)
    meta.units |= extra.get("units", {})
    meta.derivations = {
        m.value: MEASURES[m].derivation for m in plan.measures if MEASURES[m].derivation
    }
    meta.sort = extra.get("sort")
    meta.time_granularity = extra.get("time_granularity")
    meta.multi_valued = any(DIMENSIONS[d].multi_valued for d in plan.group_by)
    if meta.multi_valued:
        notes.append(
            "One trial can count in several groups, so group totals can exceed the "
            "number of trials."
        )
    if meta.total_matching == 0:
        notes.append("No trials matched the query.")
    meta.notes = notes  # Meta copied the list on construction; later notes go in here
    meta.entities = list(entities)
    viz = Visualization(
        type=viz_type, title=_title(plan), subtitle=_subtitle(meta), encoding=encoding, data=data
    )
    return QueryResponse(status="ok", visualization=viz, meta=meta)


# --- meta and titles ------------------------------------------------------------------------


def _base_meta(plan, fetched: Fetched, ts, llm, notes: list[str]) -> Meta:
    compiled = compile_plan(plan)
    params = [q.query.to_params() for q in compiled.queries]
    api_query = (
        {q.series: p for q, p in zip(compiled.queries, params, strict=True)}
        if plan.comparison
        else params[0]
    )
    coverage = [
        SeriesCoverage(
            series=name,
            total_matching=r.total_count,
            trials_counted=r.trials_counted,
            count_completeness="exact" if r.exact else "sample",
            count_method=r.method,
            records_fetched=len(r.studies),
        )
        for name, r in fetched
    ]
    counted = sum(c.trials_counted for c in coverage)
    total = sum(c.total_matching for c in coverage)
    sampled = [c for c in coverage if c.count_completeness == "sample"]
    methods = {c.count_method for c in coverage}
    method = next((m for m in _WEAKEST_FIRST if m in methods), None)
    bucket_clauses = {}
    if any(r.bucket_counts is not None for _, r in fetched):
        bucket_clauses = dict(compiled.buckets)
        dim = DIMENSIONS[plan.group_by[0]].title.lower()
        notes.append(
            f"Exact registry counts: one count query per {dim} value. Each bar cites up to "
            f"{MAX_CITATIONS} of its trials."
        )
    for c in sampled:
        where = f"{c.series}: " if c.series else ""
        notes.append(
            f"{where}Sample: counts use the first {c.trials_counted:,} of {c.total_matching:,} "
            "matching trials (API relevance order), not all of them."
        )
    if plan.comparison:
        notes.append(
            "Each series is a separate registry search; a trial matching several "
            "compared values appears in each series."
        )
    return Meta(
        plan=plan,
        filters=plan.filters.model_dump(mode="json", exclude_none=True),
        api_query=api_query,
        data_timestamp=ts,
        total_matching=total,
        trials_counted=counted,
        count_completeness=("sample" if sampled else "exact") if coverage else None,
        count_method=method,
        records_fetched=sum(c.records_fetched for c in coverage),
        citation_limit=MAX_CITATIONS,
        coverage=coverage,
        bucket_clauses=bucket_clauses,
        units={"trial_count": "trials"},
        notes=notes,
        llm=llm,
    )


def _subtitle(meta: Meta) -> str:
    """Coverage in one line, so a sample is visible on the chart itself, not only in meta."""

    def part(c: SeriesCoverage) -> str:
        name = f"{c.series}: " if c.series else ""
        if c.count_completeness == "sample":
            return f"{name}SAMPLE of {c.trials_counted:,} of {c.total_matching:,} trials"
        return f"{name}all {c.total_matching:,} trials"

    exact = meta.count_completeness == "exact"
    return ("Exact counts, " if exact else "Partly sampled, ") + "; ".join(
        part(c) for c in meta.coverage
    )


def _title(plan: QueryPlan) -> str:
    f = plan.filters
    scope = f"{f.drug} trials" if f.drug else "Trials"
    if f.condition:
        scope += f" for {f.condition}"
    if f.sponsor:
        scope += f" sponsored by {f.sponsor}"
    if f.country:
        scope += f" in {f.country}"
    if f.status:
        scope += " (" + ", ".join(s.value.replace("_", " ").lower() for s in f.status) + ")"
    if f.phase:
        scope += " in " + ", ".join(PHASE_LABELS[p.value] for p in f.phase)
    if f.start_year and f.end_year:
        scope += f", {f.start_year} to {f.end_year}"
    elif f.start_year:
        scope += f" since {f.start_year}"
    elif f.end_year:
        scope += f" through {f.end_year}"

    dims = [DIMENSIONS[d].title.lower() for d in plan.group_by]
    match plan.analysis:
        case Analysis.BREAKDOWN:
            title = f"{scope} by {' and '.join(dims)}"
        case Analysis.TREND:
            title = f"{scope} started per year"
        case Analysis.NETWORK:
            title = (
                f"{DIMENSIONS[plan.group_by[0]].title} co-occurrence network: {scope.lower()}"
                if plan.group_by[0] == plan.group_by[1]
                else f"{dims[0].capitalize()} and {dims[1]} network: {scope.lower()}"
            )
        case Analysis.HISTOGRAM:
            m = MEASURES[plan.measures[0]]
            title = f"Distribution of {m.title.lower()} ({m.unit}): {scope.lower()}"
        case Analysis.SCATTER:
            x, y = (MEASURES[m] for m in plan.measures)
            title = f"{x.title} vs {y.title.lower()}: {scope.lower()}"
        case _:
            title = f"Number of {scope.lower()}"
    if plan.comparison:
        title += ": " + " vs ".join(plan.comparison.values)
    return title[0].upper() + title[1:]


# --- shared helpers -------------------------------------------------------------------------


def _cites(citations: list[Citation]) -> list[dict]:
    """First MAX_CITATIONS distinct trials, keeping every citation of those trials."""
    keep: list[str] = []
    out = []
    for c in citations:
        if c.nct_id not in keep:
            if len(keep) == MAX_CITATIONS:
                continue
            keep.append(c.nct_id)
        out.append(c.model_dump())
    return out


def _row(field: str, label: Any, count: int, citations: list[Citation], **extra) -> dict:
    return {
        field: label,
        "trial_count": count,
        **extra,
        "citation_count": count,
        "citations": _cites(citations),
    }


def _count_channel(title: str = "Trials") -> Channel:
    return Channel(field="trial_count", type="quantitative", title=title)


def _top(buckets: list[agg.Bucket], spec: DimensionSpec, top_n: int | None, notes, what=""):
    limit = top_n or (DEFAULT_TOP_N if spec.dimension in ENTITY_DIMENSIONS else None)
    if limit is None or len(buckets) <= limit:
        return buckets
    ranked = sorted(buckets, key=lambda b: (-b.count, b.label.lower()))
    kept = {b.key for b in ranked[:limit]}
    notes.append(
        f"{len(buckets) - limit} smaller {what or spec.title.lower()} groups not shown "
        f"(top {limit} kept)."
    )
    return [b for b in buckets if b.key in kept]


def _merge(tables: list[list[agg.Bucket]], spec: DimensionSpec) -> list[agg.Bucket]:
    """Buckets summed over series, to pick and order the groups every series shows."""
    out: dict[str, agg.Bucket] = {}
    for buckets in tables:
        for b in buckets:
            out.setdefault(b.key, agg.Bucket(b.key, b.label)).count += b.count
    return agg.sort_buckets(list(out.values()), spec)


def _studies(fetched: Fetched) -> list[dict]:
    return [s for _, r in fetched for s in r.studies]


def _missing_note(missing: int, what: str, notes: list[str]) -> None:
    if missing == 1:
        notes.append(f"1 trial has no {what} and is not shown.")
    elif missing:
        notes.append(f"{missing} trials have no {what} and are not shown.")


# --- builders: each returns (encoding, data, extra meta) ------------------------------------


def _bar(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    if plan.analysis is Analysis.TOTAL:  # total with a comparison: one bar per series
        rows = [
            _row("series", name, r.total_count, agg.total(r.studies).citations)
            for name, r in fetched
        ]
        enc = {
            "x": Channel(field="series", type="nominal", title="Compared"),
            "y": _count_channel(),
        }
        return enc, rows, {"sort": "none"}

    spec = DIMENSIONS[plan.group_by[0]]
    [(_, r)] = fetched
    table = agg.breakdown(r.studies, spec, r.bucket_counts)
    _missing_note(table.missing, spec.title.lower(), notes)
    buckets = _top(table.buckets, spec, plan.top_n, notes)
    field = spec.dimension.value
    rows = [_row(field, b.label, b.count, b.citations) for b in buckets]
    cat = Channel(field=field, type=spec.channel_type, title=spec.title)
    if spec.dimension in ENTITY_DIMENSIONS or spec.dimension is Dimension.INTERVENTION_TYPE:
        enc = {"x": _count_channel(), "y": cat}  # horizontal bars, long labels
        return enc, rows, {"sort": "descending by trial_count"}
    sort = "phase order" if spec.order else "descending by trial_count"
    return {"x": cat, "y": _count_channel()}, rows, {"sort": sort}


def _grouped(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    if plan.comparison:
        spec = DIMENSIONS[plan.group_by[0]]
        per_series = {name: agg.breakdown(r.studies, spec, r.bucket_counts) for name, r in fetched}
        merged = _merge([t.buckets for t in per_series.values()], spec)
        keep = [b.key for b in _top(merged, spec, plan.top_n, notes)]
        labels = {b.key: b.label for b in merged}
        rows = []
        for name, table in per_series.items():
            by_key = {b.key: b for b in table.buckets}
            for key in keep:
                b = by_key.get(key)
                rows.append(
                    _row(
                        spec.dimension.value,
                        labels[key],
                        b.count if b else 0,
                        b.citations if b else [],
                        series=name,
                    )
                )
        enc = {
            "x": Channel(field=spec.dimension.value, type=spec.channel_type, title=spec.title),
            "y": _count_channel(),
            "series": Channel(field="series", type="nominal", title=plan.comparison.field.title()),
        }
        return enc, rows, {"sort": "phase order" if spec.order else "descending by trial_count"}

    a, b = (DIMENSIONS[d] for d in plan.group_by)
    studies = _studies(fetched)
    keep_a = {x.key for x in _top(agg.breakdown(studies, a).buckets, a, plan.top_n, notes)}
    b_buckets = agg.breakdown(studies, b).buckets
    keep_b = {x.key for x in _top(b_buckets, b, MAX_SERIES_GROUPS, notes, b.title.lower())}
    order_a = [x.key for x in agg.breakdown(studies, a).buckets if x.key in keep_a]
    cells = {(c.a.key, c.b.key): c for c in agg.crosstab(studies, a, b)}
    labels_a = {x.key: x.label for x in agg.breakdown(studies, a).buckets}
    rows = []
    for bk in [x for x in b_buckets if x.key in keep_b]:
        for ak in order_a:
            c = cells.get((ak, bk.key))
            rows.append(
                _row(
                    a.dimension.value,
                    labels_a[ak],
                    c.count if c else 0,
                    c.citations if c else [],
                    series=bk.label,
                )
            )
    enc = {
        "x": Channel(field=a.dimension.value, type=a.channel_type, title=a.title),
        "y": _count_channel(),
        "series": Channel(field="series", type="nominal", title=b.title),
    }
    return enc, rows, {"sort": "phase order" if a.order else "descending by trial_count"}


def _time_series(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    spec = DIMENSIONS[Dimension.START_YEAR]
    tables = {name: agg.breakdown(r.studies, spec) for name, r in fetched}
    years = [int(b.key) for t in tables.values() for b in t.buckets]
    rows = []
    if years:
        span = range(min(years), max(years) + 1)
        for name, t in tables.items():
            by_year = {b.key: b for b in t.buckets}
            for y in span:
                b = by_year.get(str(y))
                extra = {"estimated_count": b.estimated if b else 0}
                if plan.comparison:
                    extra["series"] = name
                rows.append(
                    _row("year", str(y), b.count if b else 0, b.citations if b else [], **extra)
                )
    _missing_note(sum(t.missing for t in tables.values()), "start date", notes)
    if any(r["estimated_count"] for r in rows):
        notes.append(
            "estimated_count is the part of trial_count whose start date the registry "
            "marks ESTIMATED (planned, not yet confirmed)."
        )
    enc = {"x": Channel(field="year", type="temporal", title="Start year"), "y": _count_channel()}
    if plan.comparison:
        enc["series"] = Channel(field="series", type="nominal", title=plan.comparison.field.title())
    return enc, rows, {"time_granularity": "year", "sort": "ascending by year"}


def _network(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    a, b = (DIMENSIONS[d] for d in plan.group_by)
    same = a.dimension == b.dimension
    net = agg.network(_studies(fetched), a, b, max_nodes_per_group=25 if same else 15)
    nodes = [
        Node(
            id=n.id,
            label=n.label,
            group=n.group,
            weight=n.count,
            citation_count=n.count,
            citations=_cites(n.citations),
        )
        for n in net.nodes
    ]
    edges = [
        Edge(
            source=e.source,
            target=e.target,
            weight=e.count,
            citation_count=e.count,
            citations=_cites(e.citations),
        )
        for e in net.edges
    ]
    notes.append(
        "Node weight = trials that include the entity. Edge weight = trials that include both "
        + ("drugs (combination or multi-arm studies)." if same else "ends.")
    )
    notes.append(
        f"Only the {25 if same else 15} most frequent entities per group are shown; "
        "entities with no links are dropped."
    )
    enc = {
        "node_id": Channel(field="id", type="nominal"),
        "node_label": Channel(field="label", type="nominal"),
        "node_color": Channel(field="group", type="nominal"),
        "node_size": Channel(field="weight", type="quantitative", title="Trials"),
        "edge_width": Channel(field="weight", type="quantitative", title="Shared trials"),
    }
    return enc, NetworkData(nodes=nodes, edges=edges), {"units": {"weight": "trials"}}


def _histogram(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    spec = MEASURES[plan.measures[0]]
    h = agg.histogram(_studies(fetched), spec)
    _missing_note(h.missing, spec.title.lower(), notes)
    rows = [_row("bin_start", b.lo, b.count, b.citations, bin_end=b.hi) for b in h.bins]
    notes.append("Bins are [bin_start, bin_end); bin_end null means open-ended.")
    enc = {
        "x": Channel(field="bin_start", type="quantitative", title=f"{spec.title} ({spec.unit})"),
        "x2": Channel(field="bin_end", type="quantitative"),
        "y": _count_channel(),
    }
    return enc, rows, {"units": {"bin_start": spec.unit, "bin_end": spec.unit}}


def _scatter(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    x, y = (MEASURES[m] for m in plan.measures)
    s = agg.scatter(_studies(fetched), x, y)
    _missing_note(s.missing, f"{x.title.lower()} or {y.title.lower()}", notes)
    rows = [
        {
            "nct_id": p.nct_id,
            "title": p.title,
            x.measure.value: p.x,
            y.measure.value: p.y,
            "citation_count": 1,
            "citations": [c.model_dump() for c in p.citations],
        }
        for p in s.points
    ]
    enc = {
        "x": Channel(field=x.measure.value, type="quantitative", title=f"{x.title} ({x.unit})"),
        "y": Channel(field=y.measure.value, type="quantitative", title=f"{y.title} ({y.unit})"),
        "tooltip": Channel(field="title", type="nominal"),
    }
    return enc, rows, {"units": {x.measure.value: x.unit, y.measure.value: y.unit}}


def _metric(plan: QueryPlan, fetched: Fetched, notes: list[str]):
    [(_, r)] = fetched
    t = agg.total(r.studies)
    rows = [_row("label", "Matching trials", r.total_count, t.citations)]
    enc = {"value": _count_channel(), "label": Channel(field="label", type="nominal")}
    return enc, rows, {}


_BUILDERS = {
    VizType.BAR_CHART: _bar,
    VizType.GROUPED_BAR_CHART: _grouped,
    VizType.TIME_SERIES: _time_series,
    VizType.NETWORK_GRAPH: _network,
    VizType.HISTOGRAM: _histogram,
    VizType.SCATTER_PLOT: _scatter,
    VizType.METRIC: _metric,
}
