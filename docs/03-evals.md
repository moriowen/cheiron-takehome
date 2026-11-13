# Evals

How we measure the system, and how the setup maps to common eval practice. The short version: the only probabilistic step is the planner (one LLM call that turns a question into a typed plan), so most evals check that plan with code. A second eval layer runs the full pipeline against the live registry and checks the response contract.

## What runs

| Command | What it checks | Cost |
|---|---|---|
| `uv run pytest` | 215 offline tests on recorded real API data, including the eval harness itself | free, about 2 s |
| `uv run python -m evals.planner_eval --runs 3` | 74 golden cases x 3 runs, plan checked field by field | about $0.32 on gpt-5.4-mini |
| `uv run python -m evals.e2e_eval` | 29 hand-written cases through the full pipeline, live registry | about $0.04 LLM, about 5 min |
| `uv run python -m evals.synth --n 45` | regenerates the synthetic cases (review before committing) | about $0.10 |

Results are committed in `evals/results/`. File names carry the model, the prompt version, the dataset version, and k, so every score can be traced to the exact code and data that produced it.

## Current results

| Run | Model | Prompt | Dataset | pass^k (k=3) | 95% CI | pass@k | Cost per success | p50 / p95 latency | Failures |
|---|---|---|---|---|---|---|---|---|---|
| Planner | gpt-5.4-mini | .3 | .2 (71 cases) | 98.6% | 92.4 to 99.8 | 100% | $0.00143 | 1.0 / 1.4 s | scope 1 (network) |
| Planner | gpt-5.4-mini | .4 | .3 (72 cases) | 100% | 94.9 to 100 | 100% | $0.00145 | 1.1 / 1.5 s | none |
| Planner | gpt-5.4-nano | .4 | .3 (72 cases) | 95.8% | 88.5 to 98.6 | 97.2% | $0.00042 | 1.3 / 2.0 s | scope 6, wrong_dimensions 1 |
| End-to-end | gpt-5.4-mini | .4 | .3 (27 cases) | 27/27 | 88 to 100 | | | 2.7 / 5.9 s | none |
| End-to-end, exact counts (D32, D33) | gpt-5.4-mini | .4 | .3 (27 cases) | 27/27 | 88 to 100 | | | 4.6 / 18.6 s | none; adds the exact-count check |
| Planner | gpt-5.4-mini | .4 | .4 (74 cases) | 100% | 95.1 to 100 | 100% | $0.00145 | 1.0 / 1.3 s | none; gate passes |
| End-to-end, entity check (D34) | gpt-5.4-mini | .4 | .4 (29 cases) | 29/29 | 88 to 100 | | | 5.9 / 22.3 s | none; adds the typo and unknown-name cases |

Earlier single-run results (prompt .2 and .3, 19 cases) are also in `evals/results/`.

## How the setup maps to common practice

| Practice | What we do | Where |
|---|---|---|
| 1. Error analysis before metrics | We have no production traffic, so the traces are eval runs. Every failure we saw was written down, then grouped into categories. Those categories are the `FailureCategory` codes every run reports: `scope`, `wrong_analysis`, `wrong_dimensions`, `wrong_measures`, `missed_filter`, `invented_filter`, `wrong_comparison`, `wrong_top_n`, `planner_error`. | `evals/planner_eval.py` |
| 2. Versioned, stratified golden set | Each case has an input, expected plan fields, and metadata: `intent`, `difficulty`, `source`. `DATASET_VERSION` is bumped on every change and recorded with every result, next to `PROMPT_VERSION`. A test requires at least 2 cases for every intent, so rare intents (cross-tab, scatter) are not drowned out. Reports break results down by intent, difficulty, and source. | `evals/planner_cases.py`, `evals/dataset.py`, `tests/test_evals.py` |
| 3. Synthetic data along dimensions | Cases are generated from tuples: intent x entity type x filter x phrasing style (terse, conversational, formal, lowercase with typo, indirect). Sampling is stratified by intent and seeded. Code derives the expected plan from the tuple, so labels are correct by construction. An LLM (gpt-5.4, not the planner model) only writes the question text, and a deterministic check rejects any question that drops an entity or a constraint. | `evals/synth.py`, `evals/data/synthetic.json` |
| 4. Deterministic assertions first | Every check is code: strict JSON schema at the API, Pydantic validation, field-by-field plan comparison, response invariants. We use no LLM judge. | all of `evals/` |
| 5. Separate retrieval from generation | Our two halves are interpretation (planner) and execution (fetch, count, build). Planner evals score interpretation alone. End-to-end evals score execution. Offline unit tests pin each execution stage (registry, aggregator, builder) on recorded data, so a failure points to one half. | `evals/planner_eval.py`, `evals/e2e_eval.py`, `tests/` |
| 6. Trajectory evals | There is no agent loop: one planner call and, at most, one repair retry. We report the retry rate, which is the step-count signal for this design. | `retry_rate` in planner results |
| 7. pass@k vs pass^k | `--runs k` runs each case k times at the production settings (temperature 0). Reports give pass@k, pass^k, and the per-run rate. The first k=3 run showed why this matters: 99.5% per run, but one case failed 1 of 3 runs. | `evals/planner_eval.py` |
| 8. Simulated users for multi-turn | Not applicable. The API is single-turn and stateless. | |
| 9. Align the LLM judge | Not applicable. There is no LLM judge, because code can check every property we grade. If we add a judge later (for example, to grade a generated caption), it gets labeled examples, a train/dev/test split, and TPR/TNR reporting. | |
| 10. Pairwise judge bias | Not applicable (no pairwise judging). Model comparisons run the same cases with deterministic checks. | |
| 11. Statistics | Every pass rate comes with a 95% Wilson interval. With 74 cases, 100% still only bounds the true rate above 95.1%. The nano vs mini comparison has overlapping intervals, so we did not treat it as a proven overall difference. The decision rests on per-intent failures instead (D29). | `evals/stats.py` |
| 12. Gate changes | `ci.yml` runs lint and the offline tests on GitHub on every push (no key needed). The planner eval gate runs locally with the key in `.env`: `uv run python -m evals.planner_eval --runs 3 --gate` exits 1 if any intent falls below its floor in `evals/thresholds.json`. Run it before committing a change to the prompt, plan schema, model settings, or evals. Per-intent floors mean a gain in one intent cannot hide a regression in another. With the current results, the gate passes mini and fails nano (cross-tab 50% is below 75%). | `.github/workflows/ci.yml`, `evals/thresholds.json` |
| 13. Online evals | Not built (nothing runs in production). The hooks exist: every response carries `meta.plan`, `meta.llm` (model, prompt version, cost), and its API query. Logging a sample of those and running the deterministic checks on them is the natural next step. | |
| 14. Cost and latency as metrics | Reports give total cost, cost per successful case, and p50/p95 latency next to pass rates. | planner and e2e results |
| 15. Close the loop | Each failure we found became a hand-written regression case (`source: regression`): the two comparison errors from the first run, and the partial-request refusal from the first k=3 run. The registry bug where "Chemotherapy" showed up as a drug became an end-to-end assertion. | `evals/planner_cases.py`, `evals/e2e_eval.py` |

## Adding a case

1. Add the case to `evals/planner_cases.py` with its `META` entry (intent, difficulty, source). For a failure seen anywhere, use `source: regression` and a comment saying where it came from.
2. Bump `DATASET_VERSION` in `evals/dataset.py`.
3. Run `uv run python -m evals.planner_eval --runs 3` and commit the results file.
