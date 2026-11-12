"""Planner evals: run each golden case through a real model and check the plan.

Run:  uv run python -m evals.planner_eval [--model M] [--runs K] [--only ID ...]
                                           [--no-synthetic] [--gate]

Reports, per docs/03-evals.md:
- pass@k (case passed in at least one of K runs) and pass^k (passed in all K runs)
- 95% Wilson intervals, results per intent / difficulty / source
- failure counts per category (the axial codes from our error analysis)
- cost per successful case, p50/p95 latency, retry rate
With --gate, exits 1 if any intent falls below evals/thresholds.json (used in CI).
Writes evals/results/planner-<model>-p<prompt>-d<dataset>-k<K>.json.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from evals import stats
from evals.dataset import DATASET_VERSION, load_cases
from trialviz.config import LLMSettings
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.planner import PlannerError
from trialviz.llm.prompt import PROMPT_VERSION
from trialviz.plan import Filters, QueryPlan
from trialviz.schemas import QueryRequest

RESULTS_DIR = Path(__file__).parent / "results"
THRESHOLDS_FILE = Path(__file__).parent / "thresholds.json"


class FailureCategory(StrEnum):
    """Failure categories from error analysis of eval runs (docs/03-evals.md)."""

    PLANNER_ERROR = "planner_error"  # no valid plan after the repair retry
    SCOPE = "scope"  # answerable marked unsupported, or the reverse
    WRONG_ANALYSIS = "wrong_analysis"
    WRONG_DIMENSIONS = "wrong_dimensions"
    WRONG_MEASURES = "wrong_measures"
    MISSED_FILTER = "missed_filter"  # expected filter absent or wrong
    INVENTED_FILTER = "invented_filter"  # filter the question did not state
    WRONG_COMPARISON = "wrong_comparison"
    WRONG_TOP_N = "wrong_top_n"


class Failure(NamedTuple):
    category: FailureCategory
    detail: str

    def __str__(self) -> str:
        return f"[{self.category}] {self.detail}"


def check_plan(plan: QueryPlan, expect: dict) -> list[Failure]:
    """Deterministic assertions only. Returns the failures; empty means the plan passes."""
    F = FailureCategory
    out: list[Failure] = []
    want_supported = expect.get("supported", True)
    if plan.supported != want_supported:
        reason = f" ({plan.unsupported_reason})" if plan.unsupported_reason else ""
        detail = f"supported: expected {want_supported}, got {plan.supported}{reason}"
        return [Failure(F.SCOPE, detail)]
    if "analysis" in expect:
        got = plan.analysis.value if plan.analysis else None
        if got != expect["analysis"]:
            out.append(Failure(F.WRONG_ANALYSIS, f"expected {expect['analysis']!r}, got {got!r}"))

    if "group_by" in expect:
        got = [d.value for d in plan.group_by]
        exp = expect["group_by"]
        same = got == exp if exp == ["start_year"] else sorted(got) == sorted(exp)
        if not same:
            out.append(Failure(F.WRONG_DIMENSIONS, f"expected {exp}, got {got}"))

    if "measures" in expect:
        got = sorted(m.value for m in plan.measures)
        if got != sorted(expect["measures"]):
            out.append(Failure(F.WRONG_MEASURES, f"expected {expect['measures']}, got {got}"))

    if "filters" in expect:
        out += _check_filters(plan.filters, expect["filters"], expect.get("allow_extra_filters"))

    if "comparison" in expect:
        exp, act = expect["comparison"], plan.comparison
        ok = (exp is None and act is None) or (
            exp is not None
            and act is not None
            and act.field == exp["field"]
            and {v.lower() for v in act.values} == {v.lower() for v in exp["values"]}
        )
        if not ok:
            got = act.model_dump() if act else None
            out.append(Failure(F.WRONG_COMPARISON, f"expected {exp}, got {got}"))

    if "top_n_set" in expect and (plan.top_n is not None) != expect["top_n_set"]:
        out.append(Failure(F.WRONG_TOP_N, f"expected set={expect['top_n_set']}, got {plan.top_n}"))
    return out


def _check_filters(filters: Filters, exp: dict, allow_extra: bool | None) -> list[Failure]:
    out = []
    actual = filters.model_dump(mode="json")
    for key, want in exp.items():
        got = actual[key]
        if isinstance(want, str):
            ok = isinstance(got, str) and want.lower() in got.lower()
        elif isinstance(want, list):
            ok = got is not None and set(got) == set(want)
        else:
            ok = got == want
        if not ok:
            out.append(
                Failure(FailureCategory.MISSED_FILTER, f"{key}: expected {want!r}, got {got!r}")
            )
    if not allow_extra:
        for key, got in actual.items():
            if key not in exp and got is not None:
                out.append(Failure(FailureCategory.INVENTED_FILTER, f"{key}: got {got!r}"))
    return out


def run_once(planner: OpenAIPlanner, case: dict) -> dict:
    request = QueryRequest(query=case["query"], **case.get("hints", {}))
    started = time.perf_counter()
    try:
        result = planner.plan(request)
        failures = check_plan(result.plan, case["expect"])
        plan, cost, attempts = result.plan.model_dump(mode="json"), result.cost_usd, result.attempts
    except PlannerError as e:
        failures = [Failure(FailureCategory.PLANNER_ERROR, str(e))]
        plan, cost, attempts = None, 0.0, e.attempts
    return {
        "passed": not failures,
        "failures": [{"category": f.category.value, "detail": f.detail} for f in failures],
        "plan": plan,
        "cost_usd": cost,
        "attempts": attempts,
        "latency_s": round(time.perf_counter() - started, 3),
    }


def summarize(
    runs: dict[str, list[bool]], *, latencies: list[float], costs: list[float], attempts: list[int]
) -> dict:
    n = len(runs)
    any_pass = sum(any(r) for r in runs.values())
    all_pass = sum(all(r) for r in runs.values())
    total_runs = sum(len(r) for r in runs.values())
    successes = sum(sum(r) for r in runs.values())
    return {
        "cases": n,
        "pass_at_k": any_pass / n if n else 0.0,
        "pass_hat_k": all_pass / n if n else 0.0,
        "pass_hat_k_ci95": stats.wilson(all_pass, n),
        "per_run_pass_rate": successes / total_runs if total_runs else 0.0,
        "per_run_ci95": stats.wilson(successes, total_runs),
        "cost_usd": sum(costs),
        "cost_per_success_usd": sum(costs) / successes if successes else None,
        "latency_p50_s": stats.percentile(latencies, 50),
        "latency_p95_s": stats.percentile(latencies, 95),
        "retry_rate": sum(a > 1 for a in attempts) / len(attempts) if attempts else 0.0,
    }


def _group_summary(cases: list[dict], results: dict[str, list[dict]], key: str) -> dict:
    groups: dict[str, dict[str, list[bool]]] = defaultdict(dict)
    for c in cases:
        groups[c[key]][c["id"]] = [r["passed"] for r in results[c["id"]]]
    out = {}
    for g, runs in sorted(groups.items()):
        n, ok = len(runs), sum(all(r) for r in runs.values())
        out[g] = {"cases": n, "pass_hat_k": ok / n, "ci95": stats.wilson(ok, n)}
    return out


def gate(by_intent: dict, overall: dict) -> list[str]:
    """Per-intent thresholds, so a win in one intent cannot hide a regression in another."""
    t = json.loads(THRESHOLDS_FILE.read_text())
    errors = []
    if overall["pass_hat_k"] < t["overall_pass_hat_k"]:
        errors.append(f"overall pass^k {overall['pass_hat_k']:.2f} < {t['overall_pass_hat_k']}")
    for intent, s in by_intent.items():
        floor = t["per_intent_pass_hat_k"].get(intent, t["default_per_intent"])
        if s["pass_hat_k"] < floor:
            errors.append(f"{intent}: pass^k {s['pass_hat_k']:.2f} < {floor}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Override LLM_MODEL.")
    parser.add_argument("--runs", type=int, default=1, help="Runs per case (k).")
    parser.add_argument("--only", nargs="*", help="Case IDs to run.")
    parser.add_argument("--no-synthetic", action="store_true")
    parser.add_argument("--gate", action="store_true", help="Exit 1 below thresholds.")
    args = parser.parse_args()

    settings = LLMSettings(**({"llm_model": args.model} if args.model else {}))
    planner = OpenAIPlanner(settings)
    cases = [
        c
        for c in load_cases(synthetic=not args.no_synthetic)
        if not args.only or c["id"] in args.only
    ]
    jobs = [c for c in cases for _ in range(args.runs)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        flat = list(pool.map(lambda c: (c["id"], run_once(planner, c)), jobs))
    results: dict[str, list[dict]] = defaultdict(list)
    for cid, r in flat:
        results[cid].append(r)

    all_runs = [r for rs in results.values() for r in rs]
    overall = summarize(
        {cid: [r["passed"] for r in rs] for cid, rs in results.items()},
        latencies=[r["latency_s"] for r in all_runs],
        costs=[r["cost_usd"] for r in all_runs],
        attempts=[r["attempts"] for r in all_runs],
    )
    by = {k: _group_summary(cases, results, k) for k in ("intent", "difficulty", "source")}
    categories = Counter(f["category"] for r in all_runs for f in r["failures"])

    for c in cases:
        rs = results[c["id"]]
        mark = "PASS" if all(r["passed"] for r in rs) else "FAIL"
        print(f"{mark} {sum(r['passed'] for r in rs)}/{len(rs)}  {c['id']}  [{c['intent']}]")
        for f in {(f["category"], f["detail"]) for r in rs for f in r["failures"]}:
            print(f"      [{f[0]}] {f[1]}")

    lo, hi = overall["pass_hat_k_ci95"]
    print(
        f"\n{settings.llm_model}  prompt={PROMPT_VERSION}  dataset={DATASET_VERSION}  "
        f"k={args.runs}  cases={overall['cases']}"
    )
    print(
        f"pass^k {overall['pass_hat_k']:.1%} (95% CI {lo:.1%}-{hi:.1%})   "
        f"pass@k {overall['pass_at_k']:.1%}   per-run {overall['per_run_pass_rate']:.1%}"
    )
    cps = overall["cost_per_success_usd"]
    print(
        f"cost ${overall['cost_usd']:.4f}  per success ${cps or 0:.5f}   "
        f"latency p50 {overall['latency_p50_s']:.2f}s p95 {overall['latency_p95_s']:.2f}s   "
        f"retry rate {overall['retry_rate']:.1%}"
    )
    print(
        "by intent: "
        + ", ".join(f"{k} {v['pass_hat_k']:.0%} ({v['cases']})" for k, v in by["intent"].items())
    )
    print(
        "failure categories: "
        + (", ".join(f"{k} {v}" for k, v in categories.most_common()) or "none")
    )

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / (
        f"planner-{settings.llm_model}-p{PROMPT_VERSION}-d{DATASET_VERSION}-k{args.runs}.json"
    )
    out.write_text(
        json.dumps(
            {
                "model": settings.llm_model,
                "prompt_version": PROMPT_VERSION,
                "dataset_version": DATASET_VERSION,
                "runs_per_case": args.runs,
                "overall": overall,
                "by": by,
                "failure_categories": dict(categories),
                "cases": {
                    c["id"]: {"intent": c["intent"], "runs": results[c["id"]]} for c in cases
                },
            },
            indent=2,
        )
    )
    print(f"wrote {out}")

    if args.gate and (errors := gate(by["intent"], overall)):
        print("GATE FAILED:\n  " + "\n  ".join(errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
