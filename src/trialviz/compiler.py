"""Plan compiler: QueryPlan -> ClinicalTrials.gov queries and the fields to fetch.

Deterministic. A comparison becomes one query per compared value (one series each). A
breakdown on one fixed-value dimension also carries that dimension's bucket clauses, so the
retriever can count each bucket in the registry instead of counting records (D32).
"""

from dataclasses import dataclass

from trialviz.ctgov.client import StudyQuery
from trialviz.plan import Analysis, Filters, QueryPlan
from trialviz.registry import BASE_FIELDS, DIMENSIONS, MEASURES


@dataclass(frozen=True)
class SeriesQuery:
    series: str | None  # compared value, or None when there is no comparison
    query: StudyQuery


@dataclass(frozen=True)
class CompiledPlan:
    queries: list[SeriesQuery]
    fields: tuple[str, ...]
    buckets: tuple[tuple[str, str], ...] = ()  # (key, Essie clause); empty: count records


def compile_plan(plan: QueryPlan) -> CompiledPlan:
    base = filters_to_query(plan.filters)
    if plan.comparison:
        attr = {"drug": "intervention", "condition": "condition", "sponsor": "sponsor"}[
            plan.comparison.field
        ]
        queries = [
            SeriesQuery(value, StudyQuery(**{**base.__dict__, attr: value}))
            for value in plan.comparison.values
        ]
    else:
        queries = [SeriesQuery(None, base)]

    fields = [*BASE_FIELDS]
    for dim in plan.group_by:
        fields += DIMENSIONS[dim].fields
    for m in plan.measures:
        fields += MEASURES[m].fields
    buckets = ()
    if plan.analysis is Analysis.BREAKDOWN and len(plan.group_by) == 1:
        buckets = DIMENSIONS[plan.group_by[0]].buckets
    return CompiledPlan(queries, tuple(dict.fromkeys(fields)), buckets)


def filters_to_query(f: Filters) -> StudyQuery:
    clauses = []
    if f.country:
        clauses.append(f'AREA[LocationCountry]"{_essie_text(f.country)}"')
    if f.phase:
        clauses.append(f"AREA[Phase]({' OR '.join(p.value for p in f.phase)})")
    if f.start_year or f.end_year:
        lo = f"{f.start_year}-01-01" if f.start_year else "MIN"
        hi = f"{f.end_year}-12-31" if f.end_year else "MAX"
        clauses.append(f"AREA[StartDate]RANGE[{lo}, {hi}]")
    return StudyQuery(
        intervention=f.drug,
        condition=f.condition,
        sponsor=f.sponsor,
        overall_status=tuple(s.value for s in f.status or ()),
        advanced=" AND ".join(clauses) or None,
    )


def _essie_text(text: str) -> str:
    """User text inside an Essie quoted string: drop characters that would break the syntax."""
    return text.replace('"', "").replace("[", "").replace("]", "").strip()
