"""Field registry: everything about one Dimension or Measure in one place (D09-D11, D15).

Each spec names the API fields it needs and extracts normalized values from a study record.
Every extracted value carries a Citation whose `excerpt` is the exact API value, so citations
are correct by construction. Adding a dimension means adding one spec here.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from trialviz.plan import Dimension, Measure
from trialviz.schemas import Citation

ABSENT = "(field absent)"
MISSING_BUCKET = "(missing)"  # bucket key for trials with no value (D32)
DRUG_TYPES = {"DRUG", "BIOLOGICAL"}
# Free-text "drug" names that name a category, not a drug. Matched only when no MeSH term exists.
GENERIC_DRUG_NAMES = {
    "chemotherapy",
    "standard of care",
    "standard chemotherapy",
    "placebo",
    "saline",
    "best supportive care",
    "control",
    "usual care",
    "vehicle",
}


@dataclass(frozen=True)
class Value:
    key: str  # grouping key (normalized)
    label: str  # display label
    citation: Citation
    estimated: bool = False  # for dates: registry marks the date ESTIMATED


@dataclass(frozen=True)
class MeasureValue:
    value: float
    citation: Citation


@dataclass(frozen=True)
class DimensionSpec:
    dimension: Dimension
    title: str
    fields: tuple[str, ...]
    extract: Callable[[dict], list[Value]]
    channel_type: Literal["nominal", "ordinal", "temporal"] = "nominal"
    order: tuple[str, ...] = ()  # fixed key order for ordinal dimensions
    multi_valued: bool = False
    # Fixed-value dimensions: (bucket key, Essie clause) for every possible value, so the
    # registry can count each bucket exactly (D32). Keys match `extract`; MISSING_BUCKET is
    # the trials with no value. Empty for open-ended dimensions (names, countries, years).
    buckets: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MeasureSpec:
    measure: Measure
    title: str
    unit: str
    fields: tuple[str, ...]
    extract: Callable[[dict], MeasureValue | None]
    bins: tuple[float, ...] = field(default=())  # histogram bin edges


# --- record access --------------------------------------------------------------------------


def get(study: dict, path: str) -> Any:
    node: Any = study
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def nct_id(study: dict) -> str:
    return get(study, "protocolSection.identificationModule.nctId")


def cite(study: dict, path: str, value: Any) -> Citation:
    excerpt = value if isinstance(value, str) else json.dumps(value)
    return Citation(
        nct_id=nct_id(study),
        field=path,
        excerpt=excerpt,
        title=get(study, "protocolSection.identificationModule.briefTitle"),
    )


def _clean_name(name: str) -> str:
    name = re.sub(r"\(.*?\)|®|™|\(R\)", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _display(label: str) -> str:
    return label[0].upper() + label[1:] if label.islower() else label


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _mesh_matches(names: list[str], meshes: list[str]) -> list[str]:
    """MeSH terms that a free-text name refers to: substring either way, or the same words in
    another order ("Non-Small Cell Lung Carcinoma" ~ "Carcinoma, Non-Small-Cell Lung")."""
    lowered = [n.lower() for n in names if len(n) >= 3]
    return [
        m
        for m in meshes
        if any(m.lower() in n or n in m.lower() or _tokens(m) == _tokens(n) for n in lowered)
    ]


# --- dimensions -----------------------------------------------------------------------------

P = "protocolSection"
PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE1|PHASE2": "Phase 1/2",
    "PHASE2": "Phase 2",
    "PHASE2|PHASE3": "Phase 2/3",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
    "NOT_REPORTED": "Not reported",
}


def _phase(s: dict) -> list[Value]:
    path = f"{P}.designModule.phases"
    phases = get(s, path)
    if not phases:
        return [Value("NOT_REPORTED", "Not reported", cite(s, path, ABSENT))]
    key = "|".join(phases)
    label = PHASE_LABELS.get(key, key.replace("|", " / "))
    return [Value(key, label, cite(s, path, phases))]


def _enum(path: str, labels: dict[str, str] | None = None) -> Callable[[dict], list[Value]]:
    """Single-valued field. With `labels=None` the raw value is the label (names)."""

    def extract(s: dict) -> list[Value]:
        v = get(s, path)
        if not v:
            return []
        label = v if labels is None else labels.get(v, v.replace("_", " ").capitalize())
        return [Value(v, label, cite(s, path, v))]

    return extract


def _start_year(s: dict) -> list[Value]:
    path = f"{P}.statusModule.startDateStruct"
    struct = get(s, path) or {}
    date = struct.get("date")
    if not date:
        return []
    return [
        Value(
            date[:4],
            date[:4],
            cite(s, f"{path}.date", date),
            estimated=struct.get("type") == "ESTIMATED",
        )
    ]


def _drugs(s: dict) -> list[Value]:
    """DRUG and BIOLOGICAL interventions, normalized to MeSH terms where possible (D11)."""
    base = f"{P}.armsInterventionsModule.interventions"
    meshes = [m["term"] for m in get(s, "derivedSection.interventionBrowseModule.meshes") or []]
    out: dict[str, Value] = {}
    for i, iv in enumerate(get(s, base) or []):
        if iv.get("type") not in DRUG_TYPES or not iv.get("name"):
            continue
        names = [iv["name"], *iv.get("otherNames", [])]
        labels = _mesh_matches(names, meshes) or [_clean_name(iv["name"])]
        for label in labels:
            key = label.lower()
            if not key or "placebo" in key or key in GENERIC_DRUG_NAMES:
                continue
            citation = cite(s, f"{base}[{i}].name", iv["name"])
            out.setdefault(key, Value(key, _display(label), citation))
    return list(out.values())


def _conditions(s: dict) -> list[Value]:
    base = f"{P}.conditionsModule.conditions"
    meshes = [m["term"] for m in get(s, "derivedSection.conditionBrowseModule.meshes") or []]
    out: dict[str, Value] = {}
    for i, cond in enumerate(get(s, base) or []):
        for label in _mesh_matches([cond], meshes) or [_clean_name(cond)]:
            key = label.lower()
            out.setdefault(key, Value(key, _display(label), cite(s, f"{base}[{i}]", cond)))
    return list(out.values())


def _countries(s: dict) -> list[Value]:
    base = f"{P}.contactsLocationsModule.locations"
    out: dict[str, Value] = {}
    for i, loc in enumerate(get(s, base) or []):
        if c := loc.get("country"):
            out.setdefault(c, Value(c, c, cite(s, f"{base}[{i}].country", c)))
    return list(out.values())


def _intervention_types(s: dict) -> list[Value]:
    base = f"{P}.armsInterventionsModule.interventions"
    out: dict[str, Value] = {}
    for i, iv in enumerate(get(s, base) or []):
        if t := iv.get("type"):
            label = t.replace("_", " ").capitalize()
            out.setdefault(t, Value(t, label, cite(s, f"{base}[{i}].type", t)))
    return list(out.values())


def _value_buckets(area: str, values: list[str]) -> tuple[tuple[str, str], ...]:
    """One bucket per API enum value, plus the trials with no value."""
    return (
        *((v, f"AREA[{area}]{v}") for v in values),
        (MISSING_BUCKET, f"AREA[{area}]MISSING"),
    )


# A phase key is the trial's whole phase list ("PHASE1|PHASE2"), so a bucket must exclude
# the neighbouring phases. Checked live: the 9 counts sum to totalCount (docs/01).
PHASE_BUCKETS = (
    ("EARLY_PHASE1", "AREA[Phase]EARLY_PHASE1"),
    ("PHASE1", "AREA[Phase]PHASE1 AND NOT AREA[Phase]PHASE2"),
    ("PHASE1|PHASE2", "AREA[Phase]PHASE1 AND AREA[Phase]PHASE2"),
    ("PHASE2", "AREA[Phase]PHASE2 AND NOT AREA[Phase]PHASE1 AND NOT AREA[Phase]PHASE3"),
    ("PHASE2|PHASE3", "AREA[Phase]PHASE2 AND AREA[Phase]PHASE3"),
    ("PHASE3", "AREA[Phase]PHASE3 AND NOT AREA[Phase]PHASE2"),
    ("PHASE4", "AREA[Phase]PHASE4"),
    ("NA", "AREA[Phase]NA"),
    ("NOT_REPORTED", "AREA[Phase]MISSING"),
)
# API enum values, from GET /studies/enums (2026-11-12).
OVERALL_STATUSES = [
    "ACTIVE_NOT_RECRUITING", "COMPLETED", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING",
    "RECRUITING", "SUSPENDED", "TERMINATED", "WITHDRAWN", "AVAILABLE", "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING", "WITHHELD", "UNKNOWN",
]  # fmt: skip
STUDY_TYPES = ["EXPANDED_ACCESS", "INTERVENTIONAL", "OBSERVATIONAL"]
SPONSOR_CLASSES = ["NIH", "FED", "OTHER_GOV", "INDIV", "INDUSTRY", "NETWORK", "AMBIG", "OTHER",
                   "UNKNOWN"]  # fmt: skip
INTERVENTION_TYPES = [
    "BEHAVIORAL", "BIOLOGICAL", "COMBINATION_PRODUCT", "DEVICE", "DIAGNOSTIC_TEST",
    "DIETARY_SUPPLEMENT", "DRUG", "GENETIC", "PROCEDURE", "RADIATION", "OTHER",
]  # fmt: skip

SPONSOR_CLASS_LABELS = {
    "INDUSTRY": "Industry",
    "NIH": "NIH",
    "FED": "Other U.S. federal",
    "OTHER_GOV": "Other government",
    "INDIV": "Individual",
    "NETWORK": "Network",
    "OTHER": "Other (academic, hospital, nonprofit)",
    "AMBIG": "Ambiguous",
    "UNKNOWN": "Unknown",
}

_INTERVENTIONS = (
    f"{P}.armsInterventionsModule.interventions.name",
    f"{P}.armsInterventionsModule.interventions.type",
    f"{P}.armsInterventionsModule.interventions.otherNames",
)

DIMENSIONS: dict[Dimension, DimensionSpec] = {
    spec.dimension: spec
    for spec in [
        DimensionSpec(
            Dimension.PHASE, "Phase", (f"{P}.designModule.phases",), _phase,
            channel_type="ordinal", order=tuple(PHASE_LABELS), buckets=PHASE_BUCKETS,
        ),
        DimensionSpec(
            Dimension.STATUS, "Overall status", (f"{P}.statusModule.overallStatus",),
            _enum(f"{P}.statusModule.overallStatus", {}),
            buckets=_value_buckets("OverallStatus", OVERALL_STATUSES),
        ),
        DimensionSpec(
            Dimension.STUDY_TYPE, "Study type", (f"{P}.designModule.studyType",),
            _enum(f"{P}.designModule.studyType", {}),
            buckets=_value_buckets("StudyType", STUDY_TYPES),
        ),
        DimensionSpec(
            Dimension.START_YEAR, "Start year", (f"{P}.statusModule.startDateStruct",),
            _start_year, channel_type="temporal",
        ),
        DimensionSpec(
            Dimension.LEAD_SPONSOR, "Lead sponsor",
            (f"{P}.sponsorCollaboratorsModule.leadSponsor.name",),
            _enum(f"{P}.sponsorCollaboratorsModule.leadSponsor.name"),
        ),
        DimensionSpec(
            Dimension.SPONSOR_CLASS, "Sponsor class",
            (f"{P}.sponsorCollaboratorsModule.leadSponsor.class",),
            _enum(f"{P}.sponsorCollaboratorsModule.leadSponsor.class", SPONSOR_CLASS_LABELS),
            buckets=_value_buckets("LeadSponsorClass", SPONSOR_CLASSES),
        ),
        DimensionSpec(
            Dimension.INTERVENTION_TYPE, "Intervention type", _INTERVENTIONS,
            _intervention_types, multi_valued=True,
            buckets=_value_buckets("InterventionType", INTERVENTION_TYPES),
        ),
        DimensionSpec(
            Dimension.DRUG, "Drug",
            (*_INTERVENTIONS, "derivedSection.interventionBrowseModule.meshes"),
            _drugs, multi_valued=True,
        ),
        DimensionSpec(
            Dimension.CONDITION, "Condition",
            (f"{P}.conditionsModule.conditions", "derivedSection.conditionBrowseModule.meshes"),
            _conditions, multi_valued=True,
        ),
        DimensionSpec(
            Dimension.COUNTRY, "Country", (f"{P}.contactsLocationsModule.locations.country",),
            _countries, multi_valued=True,
        ),
    ]
}  # fmt: skip


# --- measures -------------------------------------------------------------------------------


def _enrollment(s: dict) -> MeasureValue | None:
    path = f"{P}.designModule.enrollmentInfo.count"
    n = get(s, path)
    if n is None:
        return None
    return MeasureValue(float(n), cite(s, path, str(n)))


def _months(date: str) -> int:
    return int(date[:4]) * 12 + int(date[5:7]) - 1


def _duration_months(s: dict) -> MeasureValue | None:
    start = get(s, f"{P}.statusModule.startDateStruct.date")
    end = get(s, f"{P}.statusModule.completionDateStruct.date")
    if not start or not end or len(start) < 7 or len(end) < 7:
        return None
    months = _months(end) - _months(start)
    if months < 0:
        return None
    excerpt = f"startDate {start}; completionDate {end}"
    return MeasureValue(float(months), cite(s, f"{P}.statusModule", excerpt))


MEASURES: dict[Measure, MeasureSpec] = {
    Measure.ENROLLMENT: MeasureSpec(
        Measure.ENROLLMENT, "Enrollment", "participants",
        (f"{P}.designModule.enrollmentInfo",), _enrollment,
        bins=(0, 25, 50, 100, 200, 500, 1000, 5000),
    ),
    Measure.DURATION_MONTHS: MeasureSpec(
        Measure.DURATION_MONTHS, "Duration", "months",
        (f"{P}.statusModule.startDateStruct", f"{P}.statusModule.completionDateStruct"),
        _duration_months, bins=(0, 6, 12, 24, 36, 48, 60, 84, 120),
    ),
}  # fmt: skip

BASE_FIELDS = (f"{P}.identificationModule.nctId", f"{P}.identificationModule.briefTitle")

ALL_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *BASE_FIELDS,
            *(f for s in DIMENSIONS.values() for f in s.fields),
            *(f for s in MEASURES.values() for f in s.fields),
        ]
    )
)
