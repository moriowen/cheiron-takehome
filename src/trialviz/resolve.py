"""Entity check: count every name in the plan, and correct likely misspellings (D34, D35).

The planner copies names as the user wrote them, so a typo ("pembrolizimab") silently gives
an empty or tiny chart. This stage counts each name in the registry. A name with fewer than
MIN_MATCHES trials is compared with the registry's own autocomplete suggestions, and replaced
by the best one when that has at least STRONGER times as many trials. Code makes the choice
from registry counts; the LLM is not involved. Every check, corrected or not, is reported in
`meta.entities`, and every correction gets a note.
"""

import logging
from typing import Protocol

from pydantic import ValidationError

from trialviz.ctgov.client import CTGovError, StudyQuery
from trialviz.plan import QueryPlan
from trialviz.schemas import EntityCheck

log = logging.getLogger(__name__)

MIN_MATCHES = 20  # below this, look for a better-known spelling
STRONGER = 10  # a suggestion must match at least this many times more trials
MAX_SUGGESTIONS = 3  # suggestions counted per name

# Plan field -> (StudyQuery parameter, suggest dictionary, noun for notes)
ENTITY_FIELDS = {
    "drug": ("intervention", "InterventionName", "drug name"),
    "condition": ("condition", "Condition", "condition"),
    "sponsor": ("sponsor", "LeadSponsorName", "sponsor"),
}


class RegistryClient(Protocol):
    def count(self, query: StudyQuery) -> int: ...
    def suggest(self, text: str, dictionary: str) -> list[str]: ...


def resolve_entities(
    plan: QueryPlan, client: RegistryClient
) -> tuple[QueryPlan, list[EntityCheck]]:
    if not plan.supported:
        return plan, []
    data = plan.model_dump(mode="json")
    checks: list[EntityCheck] = []
    for field, value in data["filters"].items():
        if field in ENTITY_FIELDS and value:
            check = _check(field, value, client)
            data["filters"][field] = check.used
            checks.append(check)
    if plan.comparison and plan.comparison.field in ENTITY_FIELDS:
        values = data["comparison"]["values"]
        for i, value in enumerate(values):
            check = _check(plan.comparison.field, value, client)
            taken = {v.lower() for j, v in enumerate(values) if j != i}
            if check.corrected and check.used.lower() in taken:  # would merge two series
                check = _keep(check)
            values[i] = check.used
            checks.append(check)
    try:
        return QueryPlan.model_validate(data), checks
    except ValidationError:  # a correction broke a plan rule: keep the names as asked
        log.warning("entity corrections made the plan invalid; keeping the original names")
        return plan, [_keep(c) for c in checks]


def _check(field: str, value: str, client: RegistryClient) -> EntityCheck:
    param, dictionary, noun = ENTITY_FIELDS[field]
    matches = client.count(StudyQuery(**{param: value}))
    keep = EntityCheck(field=field, value=value, matches=matches, used=value,
                       used_matches=matches)  # fmt: skip
    if matches >= MIN_MATCHES:
        return keep
    try:
        names = client.suggest(value, dictionary)
    except CTGovError as e:
        log.warning("suggest failed for %r: %s", value, e)
        names = []
    candidates = list({n.lower(): n for n in names if n.lower() != value.lower()}.values())
    scored = [(client.count(StudyQuery(**{param: n})), n) for n in candidates[:MAX_SUGGESTIONS]]
    best_matches, best = max(scored, default=(0, None))
    if best is None or best_matches < max(1, STRONGER * matches):
        if matches == 0:
            keep.note = (
                f'No trials match "{value}" as a {noun}, and the registry suggests no close name.'
            )
        return keep
    asked = "no trials" if matches == 0 else f"only {matches:,} trial{'s' * (matches != 1)}"
    return EntityCheck(
        field=field,
        value=value,
        matches=matches,
        used=best,
        used_matches=best_matches,
        corrected=True,
        note=f'"{value}" matches {asked}; searched for "{best}" ({best_matches:,} trials), '
        f"the registry's closest {noun}, instead.",
    )


def _keep(check: EntityCheck) -> EntityCheck:
    return EntityCheck(field=check.field, value=check.value, matches=check.matches,
                       used=check.value, used_matches=check.matches)  # fmt: skip
