# Submission validation

The service uses one pipeline for planning, retrieval, aggregation, and chart construction. The evidence below distinguishes tests with known answers from checks against a changing registry.

## Offline correctness

253 tests pass. Lint and formatting pass. The six hand-labeled studies in `tests/test_cohort_correctness.py` give known answers for a cohort with several filters, overlapping comparison groups, a country ranking, a zero-filled year gap, and a combination-drug edge. Recorded registry fixtures separately check extraction, aggregation, and exact citation paths and values.

Chart rows have exported types. Response validation checks required channels, nonnegative counts, the number of supporting citations, network IDs and references, edge weights, and histogram boundaries. Network `top_n` applies per group. Ignored comparison hints produce notes. Unsupported questions avoid registry traffic.

## Planner evaluation

Prompt `2026-11-12.7`, development dataset `2026-11-12.6`, model `gpt-5.4-mini`:

- Development: 78/79 cases passed all three runs; 236/237 individual attempts passed. The existing per-intent gate passed. The failed attempt omitted a ranking limit for “leading sponsors”.
- Held-out adversarial set `2026-11-12.1`: 14/15 cases passed all three runs; 42/45 attempts passed. The forecasting case was incorrectly accepted on all attempts. The prompt and labels were not changed after scoring.

The earlier development run scored 100%. The repeat shows why a perfect saved score is not a guarantee. The held-out scope failure remains a limitation. Full results, costs, confidence intervals, and revisions are saved in `evals/results/`.

## Live end-to-end evaluation

The strengthened checks passed 34/34 development cases, with p50 latency 4.8 s and p95 22.8 s. The held-out run passed 14/15, with p50 3.2 s and p95 14.0 s. Its only failure was the same forecasting case. Each result identifies whether a failure came from interpretation, execution, or the final answer. Registry rate-limit retries occurred and are included in the measured latency.

## One failure, diagnosis, fix, and regression

An empty configured cache was silently discarded. `Retriever` selected `cache or TTLCache(3600)`, but `TTLCache.__len__` makes an empty cache false. The replacement used default settings, so the caller's size bound and TTL were lost. The fix selects the caller's cache with an explicit `cache is not None` check. `tests/test_concurrency.py::test_empty_configured_cache_is_used_and_keeps_its_bound` fills a one-entry cache, verifies its bound, and verifies a repeated query makes no extra upstream call.

Other regression evidence covers the earlier paclitaxel/formulation normalization error, both source dates for derived duration, and exact totals that fetch only a limited citation sample.

## Packaging

`scripts/package_submission.py` creates `dist/trialviz-submission.zip` from committed files with `SUBMISSION.json` recording the code revision. It excludes environment secrets and local build files. `scripts/smoke_submission.py` extracts it, installs locked dependencies, runs offline tests, then checks the actual uv and Docker Compose servers for health, OpenAPI, frontend assets, and saved examples. These startup checks use a dummy key and make no paid planner calls.

The clean ZIP at revision `0605541` passed locked dependency installation and all 253 offline tests. Both documented startup paths, uv and Docker Compose, passed HTTP checks for health, exported OpenAPI row schemas, frontend assets (including the inspector and navigation scripts), and all 15 saved examples. Test servers and Compose resources were removed afterward. The commits after that revision change documentation and validation records only; executable code, frontend files, and examples match this tested snapshot.
