"""End-to-end evals: every planner case through the full pipeline, against the live API.

Checks response invariants, not exact numbers (the registry changes daily):
- status matches `expect.supported`; the chart type matches the expected analysis
- data is non-empty; every datum has citation_count == its count and cites >= 1 trial
- every citation has an NCT ID, a field path, and a non-empty excerpt
- meta has the data timestamp, the API query, trials_counted <= total_matching, coverage per
  series that sums to the totals, and an exact count says so in the subtitle
- exact counts (D32): a phase breakdown's bars sum to total_matching (every trial has one
  phase bucket, "Not reported" included); a single-valued breakdown never sums above it
- entity check (D34): `entity_used` must be the name searched; `entity_note` must be in a note
- regression (D27): no network node is a generic intervention name such as "Chemotherapy"

Reports pass rate with a 95% Wilson interval and p50/p95 latency. Uses the hand-written cases
only (each one calls the live registry); the synthetic set is for planner evals.

Run: uv run python -m evals.e2e_eval [--model X] [--only id ...]
"""

import argparse
import json
import re
import time

from evals import stats
from evals.dataset import DATASET_VERSION, code_revision, load_cases
from evals.heldout_cases import HELDOUT_VERSION
from evals.planner_eval import RESULTS_DIR
from trialviz.config import AppSettings, LLMSettings
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.prompt import PROMPT_VERSION
from trialviz.pipeline import Pipeline
from trialviz.registry import GENERIC_DRUG_NAMES, is_drug_class
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
    if isinstance(v.data, NetworkData):
        generic = [
            n.label
            for n in v.data.nodes
            if n.group == "drug"
            and (n.label.lower() in GENERIC_DRUG_NAMES or is_drug_class(n.label))
        ]  # D27, D39: categories and drug classes are never drug nodes
        if generic:
            errors.append(f"generic intervention names as nodes: {generic}")
    if m.trials_counted > m.total_matching:
        errors.append("trials_counted > total_matching")
    if sum(c.total_matching for c in m.coverage) != m.total_matching:
        errors.append("coverage does not sum to total_matching")
    if (m.count_completeness == "exact") != (m.trials_counted == m.total_matching):
        errors.append(f"count_completeness {m.count_completeness} disagrees with counts")
    if m.count_completeness == "sample" and "SAMPLE" not in (v.subtitle or ""):
        errors.append("a sampled chart does not say so in its subtitle")
    errors += _check_exact_counts(resp)
    if want := expect.get("entity_used"):
        used = [e.used.lower() for e in m.entities]
        if want not in used:
            errors.append(f"entity check: searched {used}, expected {want!r}")
    if (want := expect.get("entity_note")) and not any(want in (e.note or "") for e in m.entities):
        errors.append(f"entity check: no note containing {want!r}")
    return errors[:10]


def _check_exact_counts(resp: QueryResponse) -> list[str]:
    m, plan = resp.meta, resp.meta.plan
    one_dim = plan.analysis.value == "breakdown" and len(plan.group_by) == 1
    if not one_dim or plan.comparison or m.multi_valued or m.count_completeness == "sample":
        return []
    total = sum(row["trial_count"] for row in resp.visualization.data)
    if total > m.total_matching:
        return [f"bars sum to {total} > total_matching {m.total_matching}"]
    cut = any("not shown" in n for n in m.notes)
    if plan.group_by[0].value == "phase" and not cut and total != m.total_matching:
        return [f"phase bars sum to {total}, total_matching is {m.total_matching}"]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--heldout", action="store_true", help="Held-out set only.")
    args = parser.parse_args()

    settings = LLMSettings(**({"llm_model": args.model} if args.model else {}))
    app = AppSettings()
    pipeline = Pipeline(
        OpenAIPlanner(settings),
        CTGovClient(rate_per_s=app.ctgov_rate_per_s, burst=app.ctgov_burst),
        max_records=app.max_records,
        sample_records=app.sample_records,
    )
    results = []
    for case in load_cases(synthetic=False, heldout=args.heldout):
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
    latencies = [r["latency_s"] for r in results]
    lo, hi = stats.wilson(passed, len(results))
    summary = {
        "model": settings.llm_model,
        "prompt_version": PROMPT_VERSION,
        "dataset_version": HELDOUT_VERSION if args.heldout else DATASET_VERSION,
        "code_revision": code_revision(),
        "passed": passed,
        "total": len(results),
        "ci95": (lo, hi),
        "latency_p50_s": stats.percentile(latencies, 50),
        "latency_p95_s": stats.percentile(latencies, 95),
        "llm_cost_usd": cost,
        "results": results,
    }
    print(
        f"\n{settings.llm_model} prompt={PROMPT_VERSION} dataset={DATASET_VERSION}: "
        f"{passed}/{len(results)} passed (95% CI {lo:.0%}-{hi:.0%}), "
        f"latency p50 {summary['latency_p50_s']:.1f}s p95 {summary['latency_p95_s']:.1f}s, "
        f"LLM cost ${cost:.4f}"
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    dataset = f"heldout{HELDOUT_VERSION}" if args.heldout else f"d{DATASET_VERSION}"
    out = RESULTS_DIR / f"e2e-{settings.llm_model}-p{PROMPT_VERSION}-{dataset}.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
