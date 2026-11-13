import json
import re
from pathlib import Path

import pytest

from trialviz.plan import Dimension, Measure
from trialviz.registry import ABSENT, ALL_FIELDS, DIMENSIONS, MEASURES, get, nct_id

FIXTURES = Path(__file__).parent / "fixtures"
EDGE = json.loads((FIXTURES / "NCT02142738.json").read_text())


def load(name: str) -> list[dict]:
    return json.loads((FIXTURES / f"{name}.json").read_text())["studies"]


ALL_STUDIES = load("pembrolizumab") + load("melanoma") + [EDGE]


def keys(dim: Dimension, study: dict) -> list[str]:
    return [v.key for v in DIMENSIONS[dim].extract(study)]


# --- one known record -----------------------------------------------------------------------


def test_phase():
    [v] = DIMENSIONS[Dimension.PHASE].extract(EDGE)
    assert (v.key, v.label) == ("PHASE3", "Phase 3")
    assert v.citation.nct_id == "NCT02142738"
    assert v.citation.field == "protocolSection.designModule.phases"
    assert v.citation.excerpt == '["PHASE3"]'
    assert v.citation.title  # brief title for display


def test_combined_and_missing_phase():
    multi = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT1"},
            "designModule": {"phases": ["PHASE1", "PHASE2"]},
        }
    }
    [v] = DIMENSIONS[Dimension.PHASE].extract(multi)
    assert (v.key, v.label) == ("PHASE1|PHASE2", "Phase 1/2")

    none = {"protocolSection": {"identificationModule": {"nctId": "NCT2"}}}
    [v] = DIMENSIONS[Dimension.PHASE].extract(none)
    assert (v.key, v.citation.excerpt) == ("NOT_REPORTED", ABSENT)


def test_start_year_and_estimated_flag():
    [v] = DIMENSIONS[Dimension.START_YEAR].extract(EDGE)
    assert (v.key, v.estimated, v.citation.excerpt) == ("2014", False, "2014-08-25")
    est = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT3"},
            "statusModule": {"startDateStruct": {"date": "2027-03", "type": "ESTIMATED"}},
        }
    }
    [v] = DIMENSIONS[Dimension.START_YEAR].extract(est)
    assert (v.key, v.estimated) == ("2027", True)


def test_drugs_are_mesh_normalized_and_cite_the_free_text_name():
    values = {v.key: v for v in DIMENSIONS[Dimension.DRUG].extract(EDGE)}
    assert set(values) == {
        "pembrolizumab",
        "paclitaxel",
        "carboplatin",
        "pemetrexed",
        "cisplatin",
        "gemcitabine",
    }
    assert values["pembrolizumab"].citation.excerpt == "Pembrolizumab"
    assert values["pembrolizumab"].citation.field.endswith("interventions[0].name")


def test_drug_name_variants_collapse():
    s = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT4"},
            "armsInterventionsModule": {
                "interventions": [
                    {"type": "DRUG", "name": "Keytruda®", "otherNames": ["pembrolizumab"]},
                    {"type": "BIOLOGICAL", "name": "MK-3475 (pembrolizumab)"},
                    {"type": "DRUG", "name": "Placebo"},
                    {"type": "DEVICE", "name": "Infusion pump"},
                ]
            },
        },
        "derivedSection": {"interventionBrowseModule": {"meshes": [{"term": "pembrolizumab"}]}},
    }
    assert keys(Dimension.DRUG, s) == ["pembrolizumab"]


def test_drug_without_mesh_falls_back_to_clean_name():
    s = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT5"},
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "XYZ-123 (R)"}]},
        }
    }
    assert keys(Dimension.DRUG, s) == ["xyz-123"]


def test_condition_matches_reordered_mesh_term():
    [v] = DIMENSIONS[Dimension.CONDITION].extract(EDGE)
    assert v.label == "Carcinoma, Non-Small-Cell Lung"
    assert v.citation.excerpt == "Non-Small Cell Lung Carcinoma"


def test_countries_are_unique_per_trial():
    s = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT6"},
            "contactsLocationsModule": {
                "locations": [{"country": "Germany"}, {"country": "Germany"}, {"country": "France"}]
            },
        }
    }
    assert keys(Dimension.COUNTRY, s) == ["Germany", "France"]
    assert keys(Dimension.COUNTRY, EDGE) == []  # this record's locations were removed


def test_sponsor_class_label():
    [v] = DIMENSIONS[Dimension.SPONSOR_CLASS].extract(EDGE)
    assert (v.key, v.label) == ("INDUSTRY", "Industry")


def test_measures():
    e = MEASURES[Measure.ENROLLMENT].extract(EDGE)
    assert (e.value, [c.excerpt for c in e.citations]) == (305.0, ["305"])
    d = MEASURES[Measure.DURATION_MONTHS].extract(EDGE)
    assert d.value == 81  # 2014-08 -> 2021-05
    # derived value: both source dates cited, each at its own path with its exact value
    assert [(c.field.split(".")[-2], c.excerpt) for c in d.citations] == [
        ("startDateStruct", "2014-08-25"),
        ("completionDateStruct", "2021-05-27"),
    ]
    assert MEASURES[Measure.DURATION_MONTHS].derivation


# --- every real record ----------------------------------------------------------------------


@pytest.mark.parametrize("dim", list(Dimension))
def test_every_dimension_extracts_from_every_record(dim):
    spec = DIMENSIONS[dim]
    hits = 0
    for s in ALL_STUDIES:
        values = spec.extract(s)
        assert len({v.key for v in values}) == len(values), "duplicate keys in one trial"
        for v in values:
            assert v.citation.nct_id == nct_id(s)
            assert v.key and v.label
        if not spec.multi_valued:
            assert len(values) <= 1
        hits += bool(values)
    assert hits > len(ALL_STUDIES) * 0.5, f"{dim} found in too few records"


def test_citation_excerpt_is_the_exact_api_value():
    """Re-read each cited path from the record and compare (D15)."""
    checked = 0
    for s in ALL_STUDIES:
        for dim in (Dimension.DRUG, Dimension.CONDITION, Dimension.COUNTRY, Dimension.STATUS):
            for v in DIMENSIONS[dim].extract(s):
                path, _, index_part = v.citation.field.partition("[")
                node = get(s, path)
                if index_part:
                    idx, _, rest = index_part.partition("]")
                    node = node[int(idx)]
                    if rest:
                        node = node[rest.lstrip(".")]
                assert node == v.citation.excerpt
                checked += 1
    assert checked > 500


def test_registry_covers_every_dimension_and_measure():
    assert set(DIMENSIONS) == set(Dimension)
    assert set(MEASURES) == set(Measure)
    assert len(ALL_FIELDS) == len(set(ALL_FIELDS))


def test_sponsor_names_keep_their_text():
    [v] = DIMENSIONS[Dimension.LEAD_SPONSOR].extract(EDGE)
    assert v.label == "Merck Sharp & Dohme LLC"


def test_lowercase_labels_are_capitalized():
    labels = {v.label for v in DIMENSIONS[Dimension.DRUG].extract(EDGE)}
    assert "Pembrolizumab" in labels
    [v] = DIMENSIONS[Dimension.STATUS].extract(EDGE)
    assert v.label == "Completed"


def test_generic_intervention_names_are_not_drugs():
    s = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT7"},
            "armsInterventionsModule": {
                "interventions": [
                    {"type": "DRUG", "name": "Chemotherapy"},
                    {"type": "DRUG", "name": "Standard of care"},
                    {"type": "DRUG", "name": "Docetaxel"},
                ]
            },
        }
    }
    assert keys(Dimension.DRUG, s) == ["docetaxel"]


def test_fixed_value_dimensions_have_a_bucket_for_every_value():
    from trialviz.registry import MISSING_BUCKET, PHASE_LABELS

    phase = DIMENSIONS[Dimension.PHASE]
    assert [k for k, _ in phase.buckets] == list(PHASE_LABELS)  # same keys as extract
    for dim in (Dimension.STATUS, Dimension.STUDY_TYPE, Dimension.SPONSOR_CLASS,
                Dimension.INTERVENTION_TYPE):  # fmt: skip
        keys = [k for k, _ in DIMENSIONS[dim].buckets]
        assert len(keys) == len(set(keys)) and keys[-1] == MISSING_BUCKET
    for dim in (Dimension.DRUG, Dimension.COUNTRY, Dimension.START_YEAR):
        assert not DIMENSIONS[dim].buckets  # open-ended: counted from records


# --- the citation contract (D15, D38) -------------------------------------------------------

_INDEX = re.compile(r"^(\w+)\[(\d+)\]$")


def resolve(study: dict, path: str):
    """Follows a citation path, including list indexes such as `interventions[2].name`."""
    node = study
    for part in path.split("."):
        if m := _INDEX.match(part):
            node = (node or {}).get(m[1]) or []
            node = node[int(m[2])] if int(m[2]) < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
    return node


def _all_citations(study):
    for spec in DIMENSIONS.values():
        for v in spec.extract(study):
            yield v.citation
    for spec in MEASURES.values():
        if mv := spec.extract(study):
            yield from mv.citations


@pytest.mark.parametrize("fixture", ["pembrolizumab", "melanoma"])
def test_every_citation_quotes_the_exact_value_at_its_path(fixture):
    """For every dimension and measure of every real record: the cited path exists and holds
    exactly the excerpt. A missing value is cited as ABSENT, and its path really is empty."""
    studies = json.loads((FIXTURES / f"{fixture}.json").read_text())["studies"] + [EDGE]
    checked = 0
    for s in studies:
        for c in _all_citations(s):
            actual = resolve(s, c.field)
            if c.excerpt == ABSENT:
                assert actual in (None, []), c
            else:
                expected = actual if isinstance(actual, str) else json.dumps(actual)
                assert c.excerpt in (expected, str(actual)), c
            assert c.nct_id == nct_id(s)
            checked += 1
    assert checked > 1000


# --- conservative name mapping (D39): regressions from the old substring rule ---------------


def drug_record(*interventions, meshes=()):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT9"},
            "armsInterventionsModule": {
                "interventions": [
                    {"type": "DRUG", "name": n, **({"otherNames": o} if o else {})}
                    for n, o in interventions
                ]
            },
        },
        "derivedSection": {"interventionBrowseModule": {"meshes": [{"term": m} for m in meshes]}},
    }


def drugs(*interventions, meshes=()):
    vals = DIMENSIONS[Dimension.DRUG].extract(drug_record(*interventions, meshes=meshes))
    return {v.label: v.citation.match for v in vals}


def test_a_formulation_is_not_its_parent_drug_or_the_reverse():
    meshes = ["Paclitaxel", "130-nm albumin-bound paclitaxel"]
    assert drugs(("Paclitaxel", None), meshes=meshes) == {"Paclitaxel": "exact"}
    assert drugs(("Nab-paclitaxel", None), meshes=["Paclitaxel"]) == {"Nab-paclitaxel": "free_text"}
    assert drugs(("Insulin", None), meshes=["Insulin Glargine"]) == {"Insulin": "free_text"}


def test_an_other_name_formulation_or_biosimilar_is_not_an_extra_drug():
    meshes = ["Etoposide", "etoposide phosphate"]
    assert drugs(("etoposide", ["Etoposide Phosphate"]), meshes=meshes) == {"Etoposide": "exact"}
    meshes = ["Rituximab", "CT-P10"]
    assert drugs(("Rituximab", ["CT-P10", "Truxima"]), meshes=meshes) == {"Rituximab": "exact"}


def test_doses_routes_and_brackets_do_not_split_a_drug():
    meshes = ["pembrolizumab"]
    for name in ["Pembrolizumab 200 mg", "Pembrolizumab Injection [Keytruda]", "Keytruda®"]:
        other = ["pembrolizumab"] if name == "Keytruda®" else None
        assert list(drugs((name, other), meshes=meshes)) == ["Pembrolizumab"], name
    assert drugs(("Pembrolizumab 200 mg", None), meshes=meshes) == {"Pembrolizumab": "exact"}
    assert drugs(("Carboplatin AUC 5", None), meshes=["Carboplatin"]) == {"Carboplatin": "exact"}


def test_list_names_map_each_drug_and_class_terms_are_dropped():
    meshes = ["Immune Checkpoint Inhibitors", "pembrolizumab", "sintilimab"]
    name = "Immune Checkpoint Inhibitors ( pembrolizumab, sintilimab)"
    assert drugs((name, None), meshes=meshes) == {"Pembrolizumab": "alias", "Sintilimab": "alias"}
    name = "Vilobelimab + pembrolizumab combination therapy"
    assert set(drugs((name, None), meshes=["vilobelimab", "pembrolizumab"])) == {
        "Vilobelimab",
        "Pembrolizumab",
    }
    name = "HBI-8000 in combination with pembrolizumab"
    assert set(drugs((name, None), meshes=["HBI-8000", "pembrolizumab"])) == {
        "HBI-8000",
        "Pembrolizumab",
    }
    assert drugs(("Chemotherapy", None)) == {} and drugs(("PD-1 inhibitor", None)) == {}


def test_condition_maps_to_a_broader_term_only_when_all_its_words_appear():
    s = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT8"},
            "conditionsModule": {"conditions": ["Stage IV Melanoma", "Uveal neoplasm"]},
        },
        "derivedSection": {
            "conditionBrowseModule": {"meshes": [{"term": "Melanoma"}, {"term": "Neoplasms"}]}
        },
    }
    got = {v.label: v.citation.match for v in DIMENSIONS[Dimension.CONDITION].extract(s)}
    assert got == {"Melanoma": "broader", "Uveal neoplasm": "free_text"}  # no "Neoplasms"
