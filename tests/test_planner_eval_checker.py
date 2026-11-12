from evals.dataset import load_cases
from evals.planner_eval import check_plan
from tests.test_plan import NO_FILTERS, valid


def test_case_ids_are_unique():
    ids = [c["id"] for c in load_cases()]
    assert len(ids) == len(set(ids))


def test_matching_plan_passes():
    plan = valid(filters=NO_FILTERS | {"drug": "Pembrolizumab (Keytruda)"})
    assert check_plan(plan, {"analysis": "breakdown", "filters": {"drug": "pembrolizumab"}}) == []


def test_wrong_analysis_fails():
    assert check_plan(valid(), {"analysis": "trend"})


def test_group_by_is_order_insensitive_except_trend():
    plan = valid(analysis="network", group_by=["drug", "lead_sponsor"])
    assert check_plan(plan, {"group_by": ["lead_sponsor", "drug"]}) == []
    assert check_plan(plan, {"group_by": ["drug", "drug"]})


def test_invented_filter_fails_unless_allowed():
    plan = valid(filters=NO_FILTERS | {"drug": "x", "country": "Germany"})
    errors = check_plan(plan, {"filters": {"drug": "x"}})
    assert any("country" in e.detail for e in errors)
    assert check_plan(plan, {"filters": {"drug": "x"}, "allow_extra_filters": True}) == []


def test_list_filters_compare_as_sets():
    plan = valid(filters=NO_FILTERS | {"drug": "x", "status": ["RECRUITING"]})
    assert check_plan(plan, {"filters": {"drug": "x", "status": ["RECRUITING"]}}) == []
    assert check_plan(plan, {"filters": {"drug": "x", "status": ["COMPLETED"]}})


def test_comparison_values_are_case_insensitive_set():
    plan = valid(filters=NO_FILTERS, comparison={"field": "drug", "values": ["B", "a"]})
    assert check_plan(plan, {"comparison": {"field": "drug", "values": ["a", "b"]}}) == []
    assert check_plan(plan, {"comparison": None})


def test_unsupported():
    plan = valid(supported=False, unsupported_reason="r", analysis=None)
    assert check_plan(plan, {"supported": False}) == []
    assert check_plan(valid(), {"supported": False})
