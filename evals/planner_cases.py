"""Planner eval cases: question -> expected plan fields.

Cases 1-9 come from the TASK.md appendix. The rest cover the other taxonomy rows in
docs/02-implementation-plan.md. Only the keys in `expect` are checked:

- supported, analysis: exact.
- group_by: exact list for trend; order-insensitive otherwise.
- measures: order-insensitive.
- filters: strings match if the expected text is contained in the actual value (case-
  insensitive); lists match as sets. Every filter not listed must be null, which catches
  invented filters. Set `allow_extra_filters: True` to skip that check.
- comparison: field exact, values as a case-insensitive set.
- top_n_set: whether top_n is non-null.
"""

CASES: list[dict] = [
    # --- TASK.md appendix ---
    {
        "id": "trend_drug_since",
        "query": "How has the number of trials for pembrolizumab changed per year since 2015?",
        "expect": {
            "analysis": "trend",
            "group_by": ["start_year"],
            "filters": {"drug": "pembrolizumab", "start_year": 2015},
        },
    },
    {
        "id": "trend_condition",
        "query": "How many trials started each year for Alzheimer's disease?",
        "expect": {
            "analysis": "trend",
            "group_by": ["start_year"],
            "filters": {"condition": "alzheimer"},
        },
    },
    {
        "id": "phase_distribution",
        "query": "How are breast cancer trials distributed across phases?",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["phase"],
            "filters": {"condition": "breast cancer"},
        },
    },
    {
        "id": "intervention_types",
        "query": "What are the most common intervention types for diabetes trials?",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["intervention_type"],
            "filters": {"condition": "diabetes"},
        },
    },
    {
        "id": "compare_drugs_phase",
        "query": "Compare phases for trials involving nivolumab vs pembrolizumab.",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["phase"],
            "comparison": {"field": "drug", "values": ["nivolumab", "pembrolizumab"]},
            "filters": {},
        },
    },
    {
        "id": "compare_conditions_sponsor_class",
        "query": "Compare sponsor categories across lung cancer and breast cancer trials.",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["sponsor_class"],
            "comparison": {"field": "condition", "values": ["lung cancer", "breast cancer"]},
            "filters": {},
        },
    },
    {
        "id": "countries_recruiting",
        "query": "Which countries have the most recruiting trials for multiple sclerosis?",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["country"],
            "filters": {"condition": "multiple sclerosis", "status": ["RECRUITING"]},
            "top_n_set": True,
        },
    },
    {
        "id": "network_sponsor_drug",
        "query": "Show a network of sponsors and drugs for melanoma trials.",
        "expect": {
            "analysis": "network",
            "group_by": ["lead_sponsor", "drug"],
            "filters": {"condition": "melanoma"},
        },
    },
    {
        "id": "network_drug_cooccurrence",
        "query": "Which drugs frequently co-occur in combination studies for lung cancer?",
        "expect": {
            "analysis": "network",
            "group_by": ["drug", "drug"],
            "filters": {"condition": "lung cancer"},
        },
    },
    # --- other taxonomy rows ---
    {
        "id": "hint_drug_trend",
        "query": "How has the number of trials for this drug changed over time?",
        "hints": {"drug_name": "Pembrolizumab"},
        "expect": {
            "analysis": "trend",
            "group_by": ["start_year"],
            "filters": {"drug": "pembrolizumab"},
        },
    },
    {
        "id": "histogram_enrollment",
        "query": "What is the distribution of enrollment sizes in phase 3 asthma trials?",
        "expect": {
            "analysis": "histogram",
            "measures": ["enrollment"],
            "filters": {"condition": "asthma", "phase": ["PHASE3"]},
        },
    },
    {
        "id": "scatter_enrollment_duration",
        "query": "Plot enrollment against trial duration for semaglutide trials.",
        "expect": {
            "analysis": "scatter",
            "measures": ["enrollment", "duration_months"],
            "filters": {"drug": "semaglutide"},
        },
    },
    {
        "id": "total_recruiting",
        "query": "How many recruiting trials are there for glioblastoma?",
        "expect": {
            "analysis": "total",
            "filters": {"condition": "glioblastoma", "status": ["RECRUITING"]},
        },
    },
    {
        "id": "top_sponsors",
        "query": "Who are the top 10 sponsors of obesity trials?",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["lead_sponsor"],
            "filters": {"condition": "obesity"},
            "top_n_set": True,
        },
    },
    {
        "id": "crosstab_phase_status",
        "query": "Break down COVID-19 trials by phase and status.",
        "expect": {
            "analysis": "breakdown",
            "group_by": ["phase", "overall_status"],
            "filters": {"condition": "covid"},
        },
    },
    {
        "id": "trend_comparison",
        "query": "Trials per year for semaglutide versus tirzepatide since 2018.",
        "expect": {
            "analysis": "trend",
            "comparison": {"field": "drug", "values": ["semaglutide", "tirzepatide"]},
            "filters": {"start_year": 2018},
        },
    },
    {
        "id": "network_drug_condition_sponsor",
        "query": "Show the network of drugs and conditions in trials sponsored by Merck.",
        "expect": {
            "analysis": "network",
            "group_by": ["drug", "condition"],
            "filters": {"sponsor": "merck"},
        },
    },
    # --- out of scope ---
    {
        "id": "unsupported_efficacy",
        "query": "Is pembrolizumab more effective than nivolumab?",
        "expect": {"supported": False},
    },
    {
        "id": "unsupported_price",
        "query": "How much does a course of semaglutide cost in the US?",
        "expect": {"supported": False},
    },
]
