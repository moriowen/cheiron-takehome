import json
from pathlib import Path

from trialviz import aggregate as agg
from trialviz.plan import Dimension, Measure
from trialviz.registry import DIMENSIONS, MEASURES

FIXTURES = Path(__file__).parent / "fixtures"
PEMBRO = json.loads((FIXTURES / "pembrolizumab.json").read_text())["studies"]
MELANOMA = json.loads((FIXTURES / "melanoma.json").read_text())["studies"]


def study(nct, *, phases=None, drugs=(), year=None, est=False, countries=(), enroll=None):
    p = {"identificationModule": {"nctId": nct, "briefTitle": f"Trial {nct}"}}
    if phases:
        p["designModule"] = {"phases": phases}
    if enroll is not None:
        p.setdefault("designModule", {})["enrollmentInfo"] = {"count": enroll}
    if drugs:
        p["armsInterventionsModule"] = {
            "interventions": [{"type": "DRUG", "name": d} for d in drugs]
        }
    if year:
        p["statusModule"] = {
            "startDateStruct": {"date": f"{year}-01", "type": "ESTIMATED" if est else "ACTUAL"}
        }
    if countries:
        p["contactsLocationsModule"] = {"locations": [{"country": c} for c in countries]}
    return {"protocolSection": p}


PHASE = DIMENSIONS[Dimension.PHASE]
DRUG = DIMENSIONS[Dimension.DRUG]


# --- breakdown ------------------------------------------------------------------------------


def test_breakdown_counts_and_cites_each_trial():
    studies = [
        study("NCT1", phases=["PHASE2"]),
        study("NCT2", phases=["PHASE2"]),
        study("NCT3", phases=["PHASE1", "PHASE2"]),
    ]
    t = agg.breakdown(studies, PHASE)
    by_key = {b.key: b for b in t.buckets}
    assert by_key["PHASE2"].count == 2
    assert [c.nct_id for c in by_key["PHASE2"].citations] == ["NCT1", "NCT2"]
    assert by_key["PHASE1|PHASE2"].label == "Phase 1/2"
    assert t.missing == 0


def test_ordinal_dimension_keeps_registry_order():
    studies = [
        study("A", phases=["PHASE3"]),
        study("B", phases=["PHASE1"]),
        study("C", phases=["PHASE3"]),
    ]
    assert [b.key for b in agg.breakdown(studies, PHASE).buckets] == ["PHASE1", "PHASE3"]


def test_nominal_dimension_sorts_by_count_then_label():
    studies = [
        study("A", countries=["France"]),
        study("B", countries=["Spain", "France"]),
        study("C", countries=["Austria"]),
    ]
    t = agg.breakdown(studies, DIMENSIONS[Dimension.COUNTRY])
    assert [(b.key, b.count) for b in t.buckets] == [("France", 2), ("Austria", 1), ("Spain", 1)]


def test_missing_values_are_counted_not_dropped_silently():
    t = agg.breakdown([study("A", countries=["France"]), study("B")], DIMENSIONS[Dimension.COUNTRY])
    assert t.missing == 1


def test_trend_splits_actual_and_estimated():
    studies = [study("A", year=2020), study("B", year=2020, est=True), study("C", year=2018)]
    t = agg.breakdown(studies, DIMENSIONS[Dimension.START_YEAR])
    assert [(b.key, b.count, b.estimated) for b in t.buckets] == [("2018", 1, 0), ("2020", 2, 1)]


def test_crosstab():
    studies = [
        study("A", phases=["PHASE2"], countries=["France"]),
        study("B", phases=["PHASE2"], countries=["France", "Spain"]),
        study("C", phases=["PHASE3"], countries=["Spain"]),
    ]
    cells = agg.crosstab(studies, PHASE, DIMENSIONS[Dimension.COUNTRY])
    got = {(c.a.key, c.b.key): c.count for c in cells}
    assert got == {("PHASE2", "France"): 2, ("PHASE2", "Spain"): 1, ("PHASE3", "Spain"): 1}


# --- network --------------------------------------------------------------------------------


def test_bipartite_network():
    studies = [
        study("A", phases=["PHASE2"], drugs=["X", "Y"]),
        study("B", phases=["PHASE2"], drugs=["X"]),
    ]
    net = agg.network(studies, PHASE, DRUG)
    nodes = {n.key: n for n in net.nodes}
    assert nodes["PHASE2"].count == 2 and nodes["x"].count == 2 and nodes["y"].count == 1
    edges = {(e.source, e.target): e for e in net.edges}
    assert edges[("phase:PHASE2", "drug:x")].count == 2
    # an edge cites both ends for each trial: the phase value and the drug name
    cites = edges[("phase:PHASE2", "drug:y")].citations
    assert [(c.nct_id, c.excerpt) for c in cites] == [("A", '["PHASE2"]'), ("A", "Y")]


def test_cooccurrence_network_pairs_drugs_within_a_trial():
    studies = [
        study("A", drugs=["X", "Y", "Z"]),
        study("B", drugs=["X", "Y"]),
        study("C", drugs=["X"]),
    ]
    net = agg.network(studies, DRUG, DRUG)
    edges = {frozenset((e.source, e.target)): e.count for e in net.edges}
    assert edges == {
        frozenset({"drug:x", "drug:y"}): 2,
        frozenset({"drug:x", "drug:z"}): 1,
        frozenset({"drug:y", "drug:z"}): 1,
    }
    # node weight counts all trials with the drug, including single-drug trials
    assert {n.key: n.count for n in net.nodes}["x"] == 3


def test_network_keeps_top_nodes_and_drops_dangling_edges():
    studies = [study(f"N{i}", drugs=["common", f"rare{i}"]) for i in range(30)]
    net = agg.network(studies, DRUG, DRUG, max_nodes_per_group=5)
    keys = {n.key for n in net.nodes}
    assert "common" in keys and len(keys) == 5
    assert all(
        e.source in {n.id for n in net.nodes} and e.target in {n.id for n in net.nodes}
        for e in net.edges
    )


def test_network_on_real_melanoma_data():
    net = agg.network(MELANOMA, DIMENSIONS[Dimension.LEAD_SPONSOR], DRUG)
    assert net.nodes and net.edges
    assert all(e.count == len({c.nct_id for c in e.citations}) for e in net.edges)


# --- measures -------------------------------------------------------------------------------


def test_histogram_bins():
    studies = [
        study("A", enroll=10),
        study("B", enroll=30),
        study("C", enroll=40),
        study("D", enroll=99999),
        study("E"),
    ]
    h = agg.histogram(studies, MEASURES[Measure.ENROLLMENT])
    got = [(b.lo, b.hi, b.count) for b in h.bins if b.count]
    assert got == [(0, 25, 1), (25, 50, 2), (5000, None, 1)]  # last bin is open-ended
    assert h.missing == 1


def test_scatter_points():
    pts = agg.scatter(PEMBRO, MEASURES[Measure.ENROLLMENT], MEASURES[Measure.DURATION_MONTHS])
    assert len(pts.points) > 50
    p = pts.points[0]
    assert len(p.citations) == 2 and p.citations[0].nct_id == p.nct_id


def test_total():
    t = agg.total(PEMBRO)
    assert t.count == len(PEMBRO) and len(t.citations) == len(PEMBRO)
