"""End-to-end evals: every planner case through the full pipeline, against the live API.

Checks response invariants, not exact numbers (the registry changes daily):
- status matches `expect.supported`; the chart type matches the expected analysis
- data is non-empty; every datum has citation_count == its count and cites >= 1 trial
- every citation has an NCT ID, a field path, and a non-empty excerpt
- meta has the data timestamp, the API query, and records_used <= total_matching

Run: uv run python -m evals.e2e_eval [--model X] [--only id ...]
"""

import argparse
import json
import re
import time

from evals.planner_cases import CASES
from evals.planner_eval import RESULTS_DIR
from trialviz.config import AppSettings, LLMSettings
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.prompt import PROMPT_VERSION
from trialviz.pipeline import Pipeline
from trialviz.schemas import NetworkData, QueryRequest, QueryResponse

NCT = re.compile(r"^NCT\d{8}$")
CHART_FOR_ANALYSIS = {
    "trend": {"time_series"},
    "breakdown": {"bar_chart", "grouped_bar_chart"},
    "network": {"network_graph"},
    "histogram": {"histogram"},
    "scatter": {"scatter_plot"},
    "total": {"metric", "bar_chart"},
}


def check_response(resp: QueryResponse, expect: dict) -> list[str]:
    errors = []
    want_ok = expect.get("supported", True)
    if (resp.status == "ok") != want_ok:
        return [f"status: expected {'ok' if want_ok else 'unsupported'}, got {resp.status}"]
    if not want_ok:
        return [] if resp.meta.notes else ["unsupported response has no reason"]

    v, m = resp.visualization, resp.meta
    if "analysis" in expect and v.type.value not in CHART_FOR_ANALYSIS[expect["analysis"]]:
        errors.append(f"chart: {v.type.value} does not fit analysis {expect['analysis']}")
    data = v.data.nodes + v.data.edges if isinstance(v.data, NetworkData) else v.data
    if not data:
        errors.append("no data")
    for d in data:
        d = d if isinstance(d, dict) else d.model_dump()
        count = d.get("trial_count", d.get("weight", d.get("citation_count")))
        if d["citation_count"] != count:
            errors.append(f"citation_count {d['citation_count']} != count {count}")
        if count and not d["citations"]:
            errors.append("datum with a count but no citations")
        for c in d["citations"]:
            if not NCT.match(c["nct_id"]) or not c["field"] or not c["excerpt"]:
                errors.append(f"bad citation {c}")
    if not m.data_timestamp or not m.api_query:
        errors.append("meta missing data_timestamp or api_query")
    if m.records_used > m.total_matching:
        errors.append("records_used > total_matching")
    return errors[:10]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()

    settings = LLMSettings(**({"llm_model": args.model} if args.model else {}))
    app = AppSettings()
    pipeline = Pipeline(
        OpenAIPlanner(settings),
        CTGovClient(min_interval_s=app.ctgov_min_interval_s),
        max_records=app.max_records,
    )
    results = []
    for case in CASES:
        if args.only and case["id"] not in args.only:
            continue
        started = time.perf_counter()
        try:
            resp = pipeline.run(QueryRequest(query=case["query"], **case.get("hints", {})))
            errors = check_response(resp, case["expect"])
            cost = resp.meta.llm.cost_usd if resp.meta.llm else 0.0
            chart = resp.visualization.type.value if resp.visualization else None
        except Exception as e:  # noqa: BLE001 - an eval records every failure
            errors, cost, chart = [f"{type(e).__name__}: {e}"], 0.0, None
        latency = round(time.perf_counter() - started, 1)
        results.append(
            {
                "id": case["id"],
                "passed": not errors,
                "errors": errors,
                "chart": chart,
                "latency_s": latency,
                "cost_usd": cost,
            }
        )
        print(f"{'PASS' if not errors else 'FAIL'}  {case['id']}  {chart}  {latency}s")
        for e in errors:
            print(f"      {e}")

    passed = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] for r in results)
    print(
        f"\n{settings.llm_model} prompt={PROMPT_VERSION}: {passed}/{len(results)} passed, "
        f"LLM cost ${cost:.4f}"
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"e2e-{settings.llm_model}-{PROMPT_VERSION}.json"
    out.write_text(
        json.dumps(
            {
                "model": settings.llm_model,
                "prompt_version": PROMPT_VERSION,
                "passed": passed,
                "total": len(results),
                "results": results,
            },
            indent=2,
        )
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
