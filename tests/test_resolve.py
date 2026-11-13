"""Entity check (D34, D35): every name in the plan is counted in the registry. A name with
no or very few matches is replaced by the registry's own suggestion when that has far more."""

from tests.test_plan import NO_FILTERS, valid
from trialviz.ctgov.client import CTGovError
from trialviz.resolve import resolve_entities

REGISTRY = {"pembrolizimab": 3, "pembrolizumab": 3006, "nivolumab": 1500, "merck": 2000}
SUGGEST = {"pembrolizimab": ["pembrolizumab"], "xqzvmab": [], "pembro": ["Pembrolizumab"]}


class FakeClient:
    def __init__(self, suggest_fails=False):
        self.suggest_fails = suggest_fails
        self.calls: list = []

    def count(self, query):
        name = query.intervention or query.condition or query.sponsor
        self.calls.append(("count", name))
        return REGISTRY.get(name.lower(), 0)

    def suggest(self, text, dictionary):
        self.calls.append(("suggest", text, dictionary))
        if self.suggest_fails:
            raise CTGovError("HTTP 404")
        return SUGGEST.get(text, [])


def drug_plan(name):
    return valid(filters=NO_FILTERS | {"drug": name})


def test_healthy_name_costs_one_count_and_is_kept():
    client = FakeClient()
    plan, checks = resolve_entities(drug_plan("pembrolizumab"), client)
    assert plan.filters.drug == "pembrolizumab"
    assert client.calls == [("count", "pembrolizumab")]
    [c] = checks
    assert (c.field, c.value, c.matches, c.used, c.corrected) == (
        "drug", "pembrolizumab", 3006, "pembrolizumab", False
    )  # fmt: skip


def test_rare_spelling_with_a_much_stronger_suggestion_is_corrected():
    plan, [c] = resolve_entities(drug_plan("pembrolizimab"), FakeClient())
    assert plan.filters.drug == "pembrolizumab"
    assert c.corrected and (c.matches, c.used_matches) == (3, 3006)
    assert "pembrolizimab" in c.note and "pembrolizumab" in c.note and "3,006" in c.note


def test_no_match_and_no_suggestion_keeps_the_name_and_says_so():
    plan, [c] = resolve_entities(drug_plan("xqzvmab"), FakeClient())
    assert plan.filters.drug == "xqzvmab" and not c.corrected
    assert c.matches == 0 and "no close name" in c.note


def test_suggest_failure_keeps_the_name():
    plan, [c] = resolve_entities(drug_plan("pembrolizimab"), FakeClient(suggest_fails=True))
    assert plan.filters.drug == "pembrolizimab" and not c.corrected


def test_zero_matches_takes_any_suggestion_with_matches():
    plan, [c] = resolve_entities(drug_plan("pembro"), FakeClient())
    assert plan.filters.drug == "Pembrolizumab" and c.corrected


def test_suggestion_that_only_changes_case_is_not_a_correction():
    SUGGEST["rarename"] = ["RareName"]
    REGISTRY["rarename"] = 2
    try:
        plan, [c] = resolve_entities(drug_plan("rarename"), FakeClient())
        assert plan.filters.drug == "rarename" and not c.corrected
    finally:
        del SUGGEST["rarename"], REGISTRY["rarename"]


def test_weaker_suggestion_is_not_taken():
    REGISTRY["rare"], REGISTRY["rarer"] = 5, 40  # 40 < 10 x 5
    SUGGEST["rare"] = ["rarer"]
    try:
        plan, [c] = resolve_entities(drug_plan("rare"), FakeClient())
        assert plan.filters.drug == "rare" and not c.corrected
    finally:
        del REGISTRY["rare"], REGISTRY["rarer"], SUGGEST["rare"]


def test_comparison_values_and_sponsor_are_checked_with_their_dictionaries():
    client = FakeClient()
    plan = valid(
        filters=NO_FILTERS | {"sponsor": "Merck"},
        comparison={"field": "drug", "values": ["pembrolizimab", "nivolumab"]},
    )
    plan, checks = resolve_entities(plan, client)
    assert plan.comparison.values == ["pembrolizumab", "nivolumab"]
    assert {c.field for c in checks} == {"drug", "sponsor"}
    assert ("suggest", "pembrolizimab", "InterventionName") in client.calls


def test_correction_that_duplicates_another_compared_value_is_skipped():
    plan = valid(
        filters=NO_FILTERS,
        comparison={"field": "drug", "values": ["pembrolizimab", "pembrolizumab"]},
    )
    plan, checks = resolve_entities(plan, FakeClient())
    assert plan.comparison.values == ["pembrolizimab", "pembrolizumab"]
    assert not any(c.corrected for c in checks)


def test_unsupported_plan_is_not_checked():
    client = FakeClient()
    plan = valid(supported=False, unsupported_reason="r", analysis=None)
    assert resolve_entities(plan, client) == (plan, [])
    assert not client.calls
