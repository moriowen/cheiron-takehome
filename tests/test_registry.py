import json
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
    assert (e.value, e.citation.excerpt) == (305.0, "305")
    d = MEASURES[Measure.DURATION_MONTHS].extract(EDGE)
    assert d.value == 81  # 2014-08 -> 2021-05
    assert "2014-08-25" in d.citation.excerpt and "2021-05-27" in d.citation.excerpt


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
