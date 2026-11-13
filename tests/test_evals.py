"""Tests for the eval harness itself: dataset rules, failure categories, statistics, synth."""

import pytest

from evals import stats
from evals.dataset import DIFFICULTIES, INTENTS, SOURCES, load_cases
from evals.planner_eval import FailureCategory, check_plan, summarize
from evals.synth import Tuple, all_tuples, expected_for, mentions_everything, sample_tuples
from tests.test_plan import NO_FILTERS, valid

# --- dataset --------------------------------------------------------------------------------


def test_every_case_has_valid_metadata_and_unique_id():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["intent"] in INTENTS and c["difficulty"] in DIFFICULTIES
        assert c["source"] in SOURCES


def test_dataset_is_stratified():
    """Rare but critical classes must not be drowned out: every intent has >= 2 cases."""
    cases = load_cases()
    for intent in INTENTS:
        assert sum(c["intent"] == intent for c in cases) >= 2, intent


# --- failure categories (axial codes) -------------------------------------------------------


def cats(plan, expect):
    return {f.category for f in check_plan(plan, expect)}


def test_failure_categories():
    plan = valid(filters=NO_FILTERS | {"drug": "x", "country": "Germany"})
    assert cats(plan, {"analysis": "trend"}) == {FailureCategory.WRONG_ANALYSIS}
    assert cats(plan, {"group_by": ["country"]}) == {FailureCategory.WRONG_DIMENSIONS}
    assert cats(plan, {"filters": {"drug": "x"}}) == {FailureCategory.INVENTED_FILTER}
    assert cats(plan, {"filters": {"drug": "y", "country": "Germany"}}) == {
        FailureCategory.MISSED_FILTER
    }
    assert cats(plan, {"supported": False}) == {FailureCategory.SCOPE}
    refused = valid(supported=False, unsupported_reason="r", analysis=None)
    assert cats(refused, {"analysis": "breakdown"}) == {FailureCategory.SCOPE}
    cmp = valid(filters=NO_FILTERS, comparison={"field": "drug", "values": ["a", "b"]})
    assert cats(cmp, {"comparison": None}) == {FailureCategory.WRONG_COMPARISON}


# --- statistics -----------------------------------------------------------------------------


def test_wilson_interval():
    lo, hi = stats.wilson(80, 100)
    assert lo == pytest.approx(0.711, abs=0.002) and hi == pytest.approx(0.867, abs=0.002)
    assert stats.wilson(0, 0) == (0.0, 1.0)
    lo, hi = stats.wilson(19, 19)
    assert hi == 1.0 and lo < 0.9  # 19/19 is not proof of 100%


def test_percentile():
    assert stats.percentile([1, 2, 3, 4, 100], 50) == 3
    assert stats.percentile([1, 2, 3, 4, 100], 95) == pytest.approx(80.8)
    assert stats.percentile([], 50) is None


def test_pass_at_k_and_pass_hat_k():
    runs = {"a": [True, True, True], "b": [True, False, True], "c": [False, False, False]}
    s = summarize(runs, latencies=[1.0, 2.0], costs=[0.1, 0.1], attempts=[1, 2])
    assert s["pass_at_k"] == pytest.approx(2 / 3)  # a, b
    assert s["pass_hat_k"] == pytest.approx(1 / 3)  # a only
    assert s["per_run_pass_rate"] == pytest.approx(5 / 9)
    assert s["retry_rate"] == 0.5
    assert s["cost_per_success_usd"] == pytest.approx(0.2 / 5)


# --- synthetic generation -------------------------------------------------------------------


def test_expected_plan_comes_from_the_tuple():
    t = Tuple("trend", "drug", "status", "terse")
    exp = expected_for(t, entity=["semaglutide"])
    assert exp["analysis"] == "trend" and exp["group_by"] == ["start_year"]
    assert exp["filters"] == {"drug": "semaglutide", "status": ["RECRUITING"]}


def test_comparison_tuple_puts_entities_in_comparison_not_filters():
    exp = expected_for(Tuple("comparison", "condition", "none", "formal"), ["asthma", "copd"])
    assert exp["comparison"] == {"field": "condition", "values": ["asthma", "copd"]}
    assert exp["filters"] == {}


def test_every_tuple_yields_a_valid_expectation():
    for t in all_tuples():
        exp = expected_for(t, ["a", "b"] if t.intent == "comparison" else ["a"])
        assert "supported" in exp or "analysis" in exp


def test_sampling_is_stratified_and_deterministic():
    a, b = sample_tuples(40, seed=7), sample_tuples(40, seed=7)
    assert a == b
    assert {t.intent for t in a} == {t.intent for t in all_tuples()}


def test_generated_question_must_mention_every_entity_and_constraint():
    t = Tuple("trend", "drug", "years", "terse")
    exp = expected_for(t, ["semaglutide"])
    assert mentions_everything("semaglutide trials per year since 2018?", exp)
    assert not mentions_everything("GLP-1 trials per year since 2018?", exp)
    assert not mentions_everything("semaglutide trials per year?", exp)


def test_constraint_disclosure_is_checked_both_ways():
    plan = valid(ignored_constraints=["randomized"])
    assert cats(plan, {"ignored_contains": ["randomiz"]}) == set()
    assert cats(plan, {}) == {FailureCategory.SPURIOUS_CONSTRAINT}
    assert cats(valid(), {"ignored_contains": ["adult"]}) == {FailureCategory.MISSED_CONSTRAINT}


def test_heldout_cases_are_never_loaded_for_development():
    dev = {c["id"] for c in load_cases()}
    held = {c["id"] for c in load_cases(heldout=True)}
    assert held and not dev & held
    assert all(c["source"] == "heldout" for c in load_cases(heldout=True))
