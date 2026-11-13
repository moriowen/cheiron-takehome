from tests.test_plan import NO_FILTERS, valid
from trialviz.compiler import compile_plan
from trialviz.registry import BASE_FIELDS


def test_filters_map_to_api_params():
    plan = valid(
        filters=NO_FILTERS
        | {
            "drug": "pembrolizumab",
            "condition": "melanoma",
            "sponsor": "Merck",
            "country": "United States",
            "phase": ["PHASE2", "PHASE3"],
            "status": ["RECRUITING"],
            "start_year": 2015,
            "end_year": 2020,
        }
    )
    [q] = compile_plan(plan).queries
    assert q.series is None
    params = q.query.to_params()
    assert params["query.intr"] == "pembrolizumab"
    assert params["query.cond"] == "melanoma"
    assert params["query.spons"] == "Merck"
    assert params["filter.overallStatus"] == "RECRUITING"
    adv = params["filter.advanced"]
    assert 'AREA[LocationCountry]"United States"' in adv
    assert "AREA[Phase](PHASE2 OR PHASE3)" in adv
    assert "AREA[StartDate]RANGE[2015-01-01, 2020-12-31]" in adv


def test_open_year_range():
    plan = valid(filters=NO_FILTERS | {"start_year": 2015})
    [q] = compile_plan(plan).queries
    assert "RANGE[2015-01-01, MAX]" in q.query.to_params()["filter.advanced"]


def test_comparison_makes_one_query_per_value():
    plan = valid(
        filters=NO_FILTERS | {"condition": "melanoma"},
        comparison={"field": "drug", "values": ["nivolumab", "pembrolizumab"]},
    )
    compiled = compile_plan(plan)
    assert [q.series for q in compiled.queries] == ["nivolumab", "pembrolizumab"]
    assert [q.query.intervention for q in compiled.queries] == ["nivolumab", "pembrolizumab"]
    assert all(q.query.condition == "melanoma" for q in compiled.queries)


def test_fields_are_only_what_the_plan_needs():
    fields = compile_plan(valid()).fields
    assert set(BASE_FIELDS) <= set(fields)
    assert "protocolSection.designModule.phases" in fields
    assert not any("locations" in f for f in fields)

    scatter = valid(analysis="scatter", group_by=[], measures=["enrollment", "duration_months"])
    assert any("enrollmentInfo" in f for f in compile_plan(scatter).fields)


def test_quotes_are_stripped_from_user_text_in_essie():
    plan = valid(filters=NO_FILTERS | {"country": 'Ger"many'})
    [q] = compile_plan(plan).queries
    assert 'AREA[LocationCountry]"Germany"' in q.query.to_params()["filter.advanced"]


def test_single_fixed_value_breakdown_carries_bucket_clauses():
    assert compile_plan(valid()).buckets[0] == ("EARLY_PHASE1", "AREA[Phase]EARLY_PHASE1")
    assert not compile_plan(valid(group_by=["country"])).buckets
    assert not compile_plan(valid(group_by=["phase", "overall_status"])).buckets
    assert not compile_plan(valid(analysis="trend", group_by=["start_year"])).buckets
