"""Aggregation: study records -> tables. Every number here is a count of trials or a value read
from a record, and every group keeps the citations of the trials in it (D15).

Chart-agnostic: the spec builders decide how to draw these tables.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from trialviz.registry import MISSING_BUCKET, DimensionSpec, MeasureSpec, Value, cite, nct_id
from trialviz.schemas import Citation


@dataclass
class Bucket:
    key: str
    label: str
    count: int = 0
    estimated: int = 0  # trials in this bucket with an ESTIMATED date (trend only)
    citations: list[Citation] = field(default_factory=list)


@dataclass
class Breakdown:
    buckets: list[Bucket]
    missing: int  # trials with no value for the dimension


@dataclass
class Cell:
    a: Bucket
    b: Bucket
    count: int
    citations: list[Citation]


@dataclass
class NetNode:
    id: str
    key: str
    label: str
    group: str
    count: int
    citations: list[Citation]


@dataclass
class NetEdge:
    source: str
    target: str
    count: int
    citations: list[Citation]


@dataclass
class Network:
    nodes: list[NetNode]
    edges: list[NetEdge]


@dataclass
class Bin:
    lo: float
    hi: float | None  # None = open-ended last bin
    count: int = 0
    citations: list[Citation] = field(default_factory=list)


@dataclass
class Histogram:
    bins: list[Bin]
    missing: int


@dataclass
class Point:
    nct_id: str
    title: str | None
    x: float
    y: float
    citations: list[Citation]


@dataclass
class Scatter:
    points: list[Point]
    missing: int


@dataclass
class Total:
    count: int
    citations: list[Citation]


# --- categorical ----------------------------------------------------------------------------


def breakdown(
    studies: list[dict], spec: DimensionSpec, counts: dict[str, int] | None = None
) -> Breakdown:
    """Counts trials per value. With `counts` (registry counts per bucket, D32), those are
    the counts, and `studies` (a few records per bucket) only supply labels and citations."""
    buckets: dict[str, Bucket] = {}
    missing = 0
    for s in studies:
        values = spec.extract(s)
        if not values:
            missing += 1
        for v in values:
            b = buckets.setdefault(v.key, Bucket(v.key, v.label))
            b.count += 1
            b.estimated += v.estimated
            b.citations.append(v.citation)
    if counts is None:
        return Breakdown(sort_buckets(list(buckets.values()), spec), missing)
    exact = []
    for key, n in counts.items():
        if key != MISSING_BUCKET and n:
            sample = buckets.get(key) or Bucket(key, key)
            exact.append(Bucket(key, sample.label, n, citations=sample.citations))
    return Breakdown(sort_buckets(exact, spec), counts.get(MISSING_BUCKET, 0))


def crosstab(studies: list[dict], a: DimensionSpec, b: DimensionSpec) -> list[Cell]:
    cells: dict[tuple[str, str], Cell] = {}
    for s in studies:
        for va in a.extract(s):
            for vb in b.extract(s):
                cell = cells.get((va.key, vb.key))
                if cell is None:
                    cell = cells[(va.key, vb.key)] = Cell(
                        Bucket(va.key, va.label), Bucket(vb.key, vb.label), 0, []
                    )
                cell.count += 1
                cell.citations += [va.citation, vb.citation]  # evidence for both dimensions
    return list(cells.values())


def sort_buckets(buckets: list[Bucket], spec: DimensionSpec) -> list[Bucket]:
    if spec.order:
        rank = {k: i for i, k in enumerate(spec.order)}
        return sorted(buckets, key=lambda b: (rank.get(b.key, len(rank)), b.key))
    if spec.channel_type == "temporal":
        return sorted(buckets, key=lambda b: b.key)
    return sorted(buckets, key=lambda b: (-b.count, b.label.lower()))


# --- network --------------------------------------------------------------------------------


def network(
    studies: list[dict], a: DimensionSpec, b: DimensionSpec, *, max_nodes_per_group: int = 15
) -> Network:
    """Bipartite network when a != b; co-occurrence network when a == b.

    Node weight = trials that include the value. Edge weight = trials that include both ends.
    Only the `max_nodes_per_group` heaviest nodes of each group are kept; nodes left without
    edges are dropped.
    """
    same = a.dimension == b.dimension
    nodes: dict[str, NetNode] = {}
    edges: dict[tuple[str, str], NetEdge] = {}

    def node(spec: DimensionSpec, v: Value) -> str:
        nid = f"{spec.dimension.value}:{v.key}"
        n = nodes.setdefault(nid, NetNode(nid, v.key, v.label, spec.dimension.value, 0, []))
        n.count += 1
        n.citations.append(v.citation)
        return nid

    for s in studies:
        if same:
            ids = {node(a, v): v for v in a.extract(s)}
            pairs = [(x, y) for x, y in combinations(sorted(ids), 2)]
            pair_values = [(ids[x], ids[y]) for x, y in pairs]
        else:
            ids_a = {node(a, v): v for v in a.extract(s)}
            ids_b = {node(b, v): v for v in b.extract(s)}
            pairs = [(x, y) for x in ids_a for y in ids_b]
            pair_values = [(ids_a[x], ids_b[y]) for x, y in pairs]
        for (x, y), (vx, vy) in zip(pairs, pair_values, strict=True):
            e = edges.setdefault((x, y), NetEdge(x, y, 0, []))
            e.count += 1
            e.citations += [vx.citation, vy.citation]

    keep: set[str] = set()
    by_group: dict[str, list[NetNode]] = defaultdict(list)
    for n in nodes.values():
        by_group[n.group].append(n)
    for group_nodes in by_group.values():
        group_nodes.sort(key=lambda n: (-n.count, n.label.lower()))
        keep |= {n.id for n in group_nodes[:max_nodes_per_group]}

    kept_edges = [e for e in edges.values() if e.source in keep and e.target in keep]
    linked = {e.source for e in kept_edges} | {e.target for e in kept_edges}
    kept_nodes = [n for n in nodes.values() if n.id in linked]
    kept_nodes.sort(key=lambda n: (n.group, -n.count, n.label.lower()))
    kept_edges.sort(key=lambda e: (-e.count, e.source, e.target))
    return Network(kept_nodes, kept_edges)


# --- measures -------------------------------------------------------------------------------


def histogram(studies: list[dict], spec: MeasureSpec) -> Histogram:
    edges = spec.bins
    bins = [Bin(lo, hi) for lo, hi in zip(edges, edges[1:], strict=False)] + [Bin(edges[-1], None)]
    missing = 0
    for s in studies:
        mv = spec.extract(s)
        if mv is None:
            missing += 1
            continue
        for b in bins:
            if mv.value >= b.lo and (b.hi is None or mv.value < b.hi):
                b.count += 1
                b.citations += mv.citations
                break
    return Histogram(bins, missing)


def scatter(studies: list[dict], x: MeasureSpec, y: MeasureSpec) -> Scatter:
    points, missing = [], 0
    for s in studies:
        vx, vy = x.extract(s), y.extract(s)
        if vx is None or vy is None:
            missing += 1
            continue
        title = vx.citations[0].title
        points.append(Point(nct_id(s), title, vx.value, vy.value, [*vx.citations, *vy.citations]))
    return Scatter(points, missing)


def total(studies: list[dict]) -> Total:
    return Total(
        len(studies),
        [cite(s, "protocolSection.identificationModule.nctId", nct_id(s)) for s in studies],
    )
