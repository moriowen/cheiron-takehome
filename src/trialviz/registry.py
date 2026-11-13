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
    citations: tuple[Citation, ...]  # every source field the value is computed from


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
    derivation: str | None = None  # how a derived value is computed from its cited fields


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


def cite(study: dict, path: str, value: Any, *, match: str | None = None) -> Citation:
    excerpt = value if isinstance(value, str) else json.dumps(value)
    return Citation(
        nct_id=nct_id(study),
        field=path,
        excerpt=excerpt,
        title=get(study, "protocolSection.identificationModule.briefTitle"),
        match=match,
    )


def _clean_name(name: str) -> str:
    name = re.sub(r"\(.*?\)|®|™|\(R\)", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _display(label: str) -> str:
    return label[0].upper() + label[1:] if label.islower() else label


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower().replace("®", " ").replace("™", " ")))


# --- name -> MeSH mapping (D39) --------------------------------------------------------------
# Conservative: a MeSH term labels a free-text name only on evidence of identity.
#   exact: the name, as words, equals the term ("Pembrolizumab" = "pembrolizumab").
#   alias: the term equals words the sponsor gave as the same thing: an `otherNames` entry, a
#          parenthesised name ("MK-3475 (pembrolizumab)"), one item of a list name
#          ("pembrolizumab, pemetrexed"), or the name's words in another order
#          ("Non-Small Cell Lung Carcinoma" = "Carcinoma, Non-Small-Cell Lung").
#   free_text: no evidence; the cleaned name itself is the label.
# A substring is not evidence: "Paclitaxel" is not "Albumin-Bound Paclitaxel", and "Insulin"
# is not "Insulin Glargine". Conditions alone may also map to a broader term whose words all
# appear in the name ("Stage IV Melanoma" -> "Melanoma"), recorded as `broader`.

MatchMethod = str  # "exact" | "alias" | "broader" | "free_text"
_LIST_SEPARATORS = re.compile(
    r"\s*(?:\bin combination with\b|\bcombined with\b|,|;|\+|/|&|\band\b|\bor\b|"
    r"\bplus\b|\bwith\b)\s*",
    re.I,
)
# A label ending in one of these words names a drug class, not a drug ("Immune Checkpoint
# Inhibitors", "Antineoplastic Agents"). Classes are never drug values (like D27's names).
_CLASS_WORDS = {
    "inhibitor", "inhibitors", "agents", "antibodies", "drugs", "therapy", "therapies",
    "immunotherapy", "chemotherapy", "regimen", "antineoplastics",
}  # fmt: skip


def is_drug_class(label: str) -> bool:
    words = _tokens(label)
    return bool(words) and words[-1] in _CLASS_WORDS


def _same_words(a: str, b: str) -> bool:
    """Same words, in any order, ignoring case and punctuation."""
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta) and sorted(ta) == sorted(tb)


def _alias_texts(name: str, other_names: list[str]) -> list[str]:
    """Texts the sponsor gave as the same intervention as `name`."""
    texts = list(other_names)
    texts += re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", name)  # "MK-3475 (pembrolizumab)"
    outside = re.sub(r"\(.*?\)|\[.*?\]", " ", name)
    texts = [t if isinstance(t, str) else "".join(t) for t in texts] + [outside]
    items = [p for t in texts for p in _LIST_SEPARATORS.split(t) if p and p.strip()]  # split first
    return [_strip(t) for t in [*texts, *items]]


def _map_names(
    name: str, other_names: list[str], meshes: list[str], *, broader: bool = False
) -> list[tuple[str, MatchMethod]]:
    """(label, method) pairs for one free-text name. Never empty: falls back to the name.
    For drugs, class terms ("... Inhibitors") are skipped, so a class name with its drugs in
    parentheses maps to the drugs."""
    if not broader:
        meshes = [m for m in meshes if not is_drug_class(m)]
    bare = _strip(name)
    same = [m for m in meshes if _same_words(m, bare)]
    if same:  # same words in the same order is exact; reordered is an alias
        return [(m, "exact" if _tokens(m) == _tokens(bare) else "alias") for m in same]
    aliases = _alias_texts(name, other_names)
    found = [m for m in meshes if any(_same_words(m, t) for t in aliases)]
    if found:
        return [(m, "alias") for m in found]
    if broader:
        words = set(_tokens(name))
        wider = [m for m in meshes if _tokens(m) and set(_tokens(m)) <= words]
        if wider:
            return [(max(wider, key=lambda m: len(_tokens(m))), "broader")]
    label = re.sub(r"\s+", " ", _STRENGTH.sub(" ", _clean_name(name))).strip(" ,;-")
    return [(label or _clean_name(name), "free_text")]


# Dose and strength ("200 mg", "75 mg/m^2", "AUC 5"), then route, dosage-form, and
# combination words: none of them is part of a drug's identity.
_STRENGTH = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|g|mcg|µg|ug|ng|ml|l|iu|units?|%)"
    r"(?:\s*/\s*(?:m\^?2|m²|kg|day|d|dose|ml))?(?![a-z])"
    r"|\b(?:auc|area under the curve)\s*(?:=\s*)?\d+(?:\.\d+)?",
    re.I,
)
_FORM = re.compile(
    r"\b(?:injections?|tablets?|capsules?|oral|intravenous|iv|infusion|subcutaneous|"
    r"solution|for injection|i\.v\.|combination therapy|combination|monotherapy)\b",
    re.I,
)


def _strip(name: str) -> str:
    """The name without parenthesised or bracketed text, doses, routes, or dosage forms."""
    name = re.sub(r"\(.*?\)|\[.*?\]|®|™|\(R\)", " ", name)
    return re.sub(r"\s+", " ", _FORM.sub(" ", _STRENGTH.sub(" ", name))).strip(" ,;-")


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
        for label, method in _map_names(iv["name"], iv.get("otherNames", []), meshes):
            key = label.lower()
            if not key or "placebo" in key or key in GENERIC_DRUG_NAMES or is_drug_class(key):
                continue
            citation = cite(s, f"{base}[{i}].name", iv["name"], match=method)
            out.setdefault(key, Value(key, _display(label), citation))
    return list(out.values())


def _conditions(s: dict) -> list[Value]:
    base = f"{P}.conditionsModule.conditions"
    meshes = [m["term"] for m in get(s, "derivedSection.conditionBrowseModule.meshes") or []]
    out: dict[str, Value] = {}
    for i, cond in enumerate(get(s, base) or []):
        for label, method in _map_names(cond, [], meshes, broader=True):
            key = label.lower()
            citation = cite(s, f"{base}[{i}]", cond, match=method)
            out.setdefault(key, Value(key, _display(label), citation))
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
    return MeasureValue(float(n), (cite(s, path, str(n)),))


def _months(date: str) -> int:
    return int(date[:4]) * 12 + int(date[5:7]) - 1


_START = f"{P}.statusModule.startDateStruct.date"
_COMPLETION = f"{P}.statusModule.completionDateStruct.date"
DURATION_DERIVATION = (
    f"(year*12 + month of {_COMPLETION}) - (year*12 + month of {_START}); days are ignored. "
    "Trials with a date missing, without a month, or ending before they start are left out."
)


def _duration_months(s: dict) -> MeasureValue | None:
    """Derived: both source dates are cited, each with its exact value."""
    start, end = get(s, _START), get(s, _COMPLETION)
    if not start or not end or len(start) < 7 or len(end) < 7:
        return None
    months = _months(end) - _months(start)
    if months < 0:
        return None
    return MeasureValue(float(months), (cite(s, _START, start), cite(s, _COMPLETION, end)))


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
        derivation=DURATION_DERIVATION,
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
