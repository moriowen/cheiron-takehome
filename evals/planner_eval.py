"""Planner evals: run each case through a real model and check the plan.

Run:  uv run python -m evals.planner_eval [--model gpt-5.4-mini] [--only case_id ...]
Writes evals/results/planner-<model>-<prompt_version>.json and prints a summary.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals.planner_cases import CASES
from trialviz.config import LLMSettings
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.planner import PlannerError
from trialviz.llm.prompt import PROMPT_VERSION
from trialviz.plan import Filters, QueryPlan
from trialviz.schemas import QueryRequest

RESULTS_DIR = Path(__file__).parent / "results"


def check_plan(plan: QueryPlan, expect: dict) -> list[str]:
    """Returns a list of mismatches; empty means the plan meets the expectation."""
    errors: list[str] = []
    for key in ("supported", "analysis"):
        if key in expect:
            actual = getattr(plan, key)
            actual = actual.value if hasattr(actual, "value") else actual
            if actual != expect[key]:
                errors.append(f"{key}: expected {expect[key]!r}, got {actual!r}")

    if "group_by" in expect:
        actual = [d.value for d in plan.group_by]
        exp = expect["group_by"]
        same = actual == exp if exp == ["start_year"] else sorted(actual) == sorted(exp)
        if not same:
            errors.append(f"group_by: expected {exp}, got {actual}")

    if "measures" in expect:
        actual = sorted(m.value for m in plan.measures)
        if actual != sorted(expect["measures"]):
            errors.append(f"measures: expected {expect['measures']}, got {actual}")

    if "filters" in expect:
        errors += _check_filters(plan.filters, expect["filters"], expect.get("allow_extra_filters"))

    if "comparison" in expect:
        exp, act = expect["comparison"], plan.comparison
        if exp is None and act is not None:
            errors.append(f"comparison: expected none, got {act.model_dump()}")
        elif exp is not None and (
            act is None
            or act.field != exp["field"]
            or {v.lower() for v in act.values} != {v.lower() for v in exp["values"]}
        ):
            errors.append(f"comparison: expected {exp}, got {act and act.model_dump()}")

    if "top_n_set" in expect and (plan.top_n is not None) != expect["top_n_set"]:
        errors.append(f"top_n: expected set={expect['top_n_set']}, got {plan.top_n}")
    return errors


def _check_filters(filters: Filters, exp: dict, allow_extra: bool | None) -> list[str]:
    errors = []
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
            errors.append(f"filters.{key}: expected {want!r}, got {got!r}")
    if not allow_extra:
        for key, got in actual.items():
            if key not in exp and got is not None:
                errors.append(f"filters.{key}: expected null, got {got!r}")
    return errors


def run_case(planner: OpenAIPlanner, case: dict) -> dict:
    request = QueryRequest(query=case["query"], **case.get("hints", {}))
    started = time.perf_counter()
    try:
        result = planner.plan(request)
    except PlannerError as e:
        return {"id": case["id"], "passed": False, "errors": [f"planner error: {e}"],
                "cost_usd": 0.0, "attempts": e.attempts}  # fmt: skip
    errors = check_plan(result.plan, case["expect"])
    return {
        "id": case["id"],
        "passed": not errors,
        "errors": errors,
        "plan": result.plan.model_dump(mode="json"),
        "attempts": result.attempts,
        "cost_usd": result.cost_usd,
        "latency_s": round(time.perf_counter() - started, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Override LLM_MODEL.")
    parser.add_argument("--only", nargs="*", help="Case IDs to run.")
    args = parser.parse_args()

    settings = LLMSettings(**({"llm_model": args.model} if args.model else {}))
    planner = OpenAIPlanner(settings)
    cases = [c for c in CASES if not args.only or c["id"] in args.only]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: run_case(planner, c), cases))

    passed = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] for r in results)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['id']}  (attempts={r.get('attempts')})")
        for e in r["errors"]:
            print(f"      {e}")
    print(f"\n{settings.llm_model} prompt={PROMPT_VERSION}: {passed}/{len(results)} passed, "
          f"cost ${cost:.4f}")  # fmt: skip

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"planner-{settings.llm_model}-{PROMPT_VERSION}.json"
    summary = {
        "model": settings.llm_model,
        "prompt_version": PROMPT_VERSION,
        "passed": passed,
        "total": len(results),
        "cost_usd": cost,
        "results": results,
    }
    out.write_text(json.dumps(summary, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
