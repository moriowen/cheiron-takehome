# Decision log

Each entry records a decision, why we made it, and what we rejected. When a decision changes, mark the old entry `Superseded by Dnn` and add a new entry. Do not edit history. Assumptions are listed separately at the end, because they can turn out false without anyone deciding anything.

Status values: `Accepted`, `Superseded by Dnn`, `Proposed`.

## Decisions

### D01. Python 3.12, FastAPI, Pydantic v2, httpx, uv
2026-11-12. Accepted.
Pydantic gives request and response schemas, validation, and OpenAPI docs from one source, and schema documentation is graded. uv gives a lock file and one setup command. Rejected: TypeScript with Zod (equal fit, but Python is faster for us to write and test).

### D02. The LLM produces only a typed `QueryPlan`
2026-11-12. Accepted.
The LLM maps a question to a plan (analysis kind, dimensions, filters, comparison). Code does fetching, counting, chart choice, titles, and citations. The LLM never sees API data and never writes a number. This is the main guard against hallucination, and the rubric weights it at 20%. Rejected: a tool-calling agent loop (more failure modes, hard to make deterministic, hard to eval) and free LLM spec generation (numbers could be invented).

### D03. OpenAI Structured Outputs with a strict JSON schema, then Pydantic validation, then one repair retry
2026-11-12. Accepted.
The strict schema guarantees shape. Pydantic validators check rules a schema cannot (year order, distinct comparison values). If validation fails, the errors go back to the model once. Refusals and truncated output fail at once. Rejected: prompt-only JSON (no guarantee), function calling (same result, more ceremony).

### D04. Official `openai` SDK behind a `Planner` protocol; no agent framework
2026-11-12. Accepted.
One LLM call does not justify LangChain or similar. `OpenAIPlanner` is the only module that imports `openai`. `StubPlanner` serves tests.

### D05. Dev model `gpt-5.4-nano`; final model chosen by evals
2026-11-12. Superseded by D21 (default model only; the supported-model set still holds).
Supported models are `gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.1`, because they accept the same request (`max_completion_tokens`, `temperature=0`, `reasoning_effort="none"`, strict `json_schema`). Probed on the API. `gpt-4o-mini` is cheaper ($0.15/$0.60 vs $0.20/$1.25 per 1M) but uses an older parameter set, so a later swap would break the request code. `gpt-5` and `gpt-5-mini` reject `temperature=0` and `reasoning_effort="none"`. A planner call costs about $0.0002 on nano. Changing the model is one setting (`LLM_MODEL`).

### D06. Track LLM spend ourselves
2026-11-12. Accepted.
The project key cannot read billing (credit endpoints need a browser session, the costs API needs `api.usage.read`). Every call logs tokens and estimated cost from a price table. The eval runner prints run cost. Rate limits (30k RPM, 150M TPM) are not a constraint.

### D07. Thin, generic ClinicalTrials.gov client
2026-11-12. Accepted.
`CTGovClient` knows API parameters, paging, and errors, and nothing about plans or charts. It throttles requests, backs off on 429/5xx, and honors `Retry-After`, because a burst of calls got HTTP 429 and the limit is not documented anywhere we could find.

### D08. Fetch slim records up to a cap and count locally
2026-11-12. Accepted. Partly superseded by D32 (exact bucket counts) and D33 (fetch every record up to 20,000).
We fetch matching studies with a `fields=` projection up to a cap (default 3,000) and aggregate in code. `meta` reports records used, total matches, and whether the fetch was truncated. Why: it gives one consistent counting method for every chart type, it is the only way to build networks, and it gives citations for every datum. Rejected: one exact `count()` call per bucket. It cannot produce citations, it costs many calls against an undocumented rate limit, and multi-phase trials make bucket counts disagree with the total (pembrolizumab per-phase counts sum to 3,295 while the total is 3,006).

### D09. Multi-valued fields
2026-11-12. Accepted.
Phase uses combined buckets ("Phase 1/2"), because the registry records them as one design. Countries, drugs, conditions, and sponsors count a trial once in each bucket it belongs to. `meta.multi_valued` is true and a note says bars can sum to more than the trial count.

### D10. Estimated dates are kept and marked
2026-11-12. Accepted.
Start dates can be `ESTIMATED` and in the future. Time buckets carry `actual` and `estimated` counts so a renderer can draw them differently. Years are the only time granularity, because 35% of dates have month precision only.

### D11. Drug identity is the intervention MeSH term
(Matching rule superseded by D39.)
2026-11-12. Accepted.
Free-text names vary (`Keytruda®`, `MK-3475`, `cisplatin` vs `Cisplatin`). NLM's MeSH terms in `derivedSection` normalize them. Fallback when no MeSH term: the lowercased free-text name. DRUG and BIOLOGICAL intervention types both count as drugs.

### D12. Our own visualization spec, rendered by D3
2026-11-12. Accepted.
Shape follows TASK.md: `visualization {type, title, encoding, data}` plus `meta`. Rejected: Vega-Lite, because network graphs do not fit it and the task asks for this shape.

### D13. Chart type is chosen by rules in code
2026-11-12. Accepted.
The LLM classifies the question (`analysis` kind and dimensions). A deterministic selector maps that shape to a chart type. The LLM never picks a chart type directly, so the same plan always gives the same chart.

### D14. Non-chart answers are explicit
2026-11-12. Accepted.
A single-number answer is a `metric` visualization. A question the registry cannot answer returns `status: "unsupported"` with a reason, not an error and not a forced chart.

### D15. Deep citations on every datum
2026-11-12. Accepted.
Each bar, bucket, node, and edge carries `citations: [{nct_id, field, excerpt}]` and `citation_count`. The excerpt is the exact field value from the API response, so it is correct by construction. Citations per datum are capped (default 5); `citation_count` gives the full number.

### D16. Test data is recorded real API responses
2026-11-12. Accepted.
Registry, aggregator, and spec tests run offline against JSON fixtures recorded once from the live API. Fast, deterministic, no rate-limit risk, and not invented data. Live tests are marked `live` and skipped by default.

### D17. Frontend is plain static HTML, JS, and D3
2026-11-12. Accepted.
FastAPI serves it locally. A static copy with preset JSON answers is deployed so reviewers can see it without running the backend. Rejected: React with Vite (a build step to explain, no benefit for a secondary deliverable).

### D18. Docker after the service exists
2026-11-12. Accepted.
A Dockerfile and compose file make `docker compose up` with a `.env` the only setup for reviewers. `uv run` stays the dev path.

### D19. The assignment prompt stays out of the public repo
2026-11-12. Accepted.
`TASK.md` is git-ignored because companies often keep take-home prompts private.

### D20. A comparison is the series, never a dimension or a filter
2026-11-12. Accepted.
First planner eval (nano, prompt .2): 17/19. Both failures were comparisons: the model put the compared field into `group_by` or added a second dimension. Validators now reject a comparison field that is also in `group_by` or `filters`, and a comparison breakdown must have exactly one dimension. The repair retry then fixes such plans. Prompt .3 states the rule with an example.

### D21. Default model is `gpt-5.4-mini`
2026-11-12. Accepted.
Planner evals, prompt .3, 19 cases: nano 18/19 ($0.0073 per run, unstable on the cross-tab case across runs), mini 19/19 ($0.0268 per run, about $0.0014 per query). The quality gain is worth the cost. Nano stays available via `LLM_MODEL` for cheap iteration. Results: `evals/results/`.

### D22. Structured hints override the LLM's filters
2026-11-12. Accepted.
After planning, code copies each request hint (`drug_name`, `phase`, ...) into the plan filters, then re-validates the plan. A hint is a fact from the caller, so it wins over what the LLM read from the text. A hint for the field being compared is skipped. A hint that makes the plan invalid (for example `start_year` after the question's end year) returns HTTP 422.

### D23. A `total` answer uses the registry count
2026-11-12. Accepted.
For a single number, the value is the API's `totalCount` (exact), and only 25 records are fetched to cite. Every other analysis counts fetched records (D08).

### D24. Readability caps
2026-11-12. Accepted.
Entity breakdowns (sponsor, drug, condition, country) keep the top 15 groups unless the user gives a number. A cross-tab keeps 8 series. Networks keep the 15 heaviest nodes per group (25 for a co-occurrence network) and drop nodes left without links. Every cap adds a note to `meta.notes`. Each datum cites at most 5 trials; `citation_count` is the full number.

### D25. HTTP errors
2026-11-12. Accepted.
Invalid request: 422 (FastAPI validation, or D22 conflicts). Planner failure or ClinicalTrials.gov failure: 502 with the cause. An unanswerable question is not an error: 200 with `status: "unsupported"` (D14).

### D26. Static demo on GitHub Pages
2026-11-12. Accepted.
GitHub Pages serves the repo root from `main` (`.nojekyll` keeps files as they are). The frontend calls `../health`; when no backend answers, it replays `examples/*.json`, which are real system outputs. With the backend, FastAPI serves the same page at `/ui/` and mounts `/examples`, so one frontend works in both modes. URL: https://moriowen.github.io/cheiron-takehome/frontend/

### D27. Generic intervention names are not drugs
2026-11-12. Accepted.
Live review of the lung cancer co-occurrence network showed "Chemotherapy" as a drug node: sponsors enter category words as DRUG interventions, and they have no MeSH term. A short stoplist (`GENERIC_DRUG_NAMES` in the registry) drops them.

### D28. Eval methodology
2026-11-12. Accepted.
Evals follow the practices mapped in [03-evals.md](03-evals.md): failure categories from error analysis, a versioned and stratified golden set (`DATASET_VERSION` with intent, difficulty, and source metadata), synthetic cases generated from explicit dimension tuples with code-derived labels, deterministic checks only (no LLM judge), pass@k and pass^k with k=3, Wilson confidence intervals, cost per success and p50/p95 latency, a per-intent gate (run locally with `--gate`, D31), and regression cases for every failure seen. Multi-turn simulation, judge alignment, pairwise judging, and online evals do not apply to a single-turn system with no judge and no production traffic.

### D29. Keep `gpt-5.4-mini` after the k=3 comparison
2026-11-12. Accepted. Refines D21.
On 72 cases x 3 runs (prompt .4): mini pass^k 100% (95% CI 94.9 to 100), nano 95.8% (88.5 to 98.6) at 29% of the cost per success. The intervals overlap, so the overall difference is not proven. But nano's failures concentrate in two intents (network scope refusals, cross-tab), and the per-intent gate fails it on cross-tab. Mini stays the default.

### D30. Answer the answerable part of a request
2026-11-12. Accepted. Prompt .4.
The first k=3 run found a planner that marked a whole network question unsupported because it also asked for trial names. The prompt now says to plan the answerable part and keep `supported=true`. Trial titles are already in every citation. A regression case pins this.

### D31. The eval gate runs locally, not on GitHub
2026-11-12. Accepted. Supersedes the `eval-gate.yml` workflow from D28.
The OpenAI key lives only in the git-ignored `.env`. A GitHub gate would need the key stored as a repository secret. The user chose to keep evals local: `uv run python -m evals.planner_eval --runs 3 --gate` before committing prompt, plan, or model changes. GitHub CI keeps lint and offline tests, which need no key.

### D32. Exact registry counts per bucket for fixed-value dimensions
2026-11-12. Accepted. Supersedes the rejection of per-bucket counts in D08.
A breakdown on one fixed-value dimension (phase, overall status, study type, sponsor class, intervention type) can count each bucket in the registry: the user's filters AND the bucket's Essie clause, with `countTotal=true`. Each dimension lists a clause for every API enum value plus a `MISSING` bucket (`registry.py`). The three reasons D08 gave against this no longer hold:
- Citations: the count call asks for 5 records (`pageSize=5`), so each bucket's count and its citations come from the same query.
- Multi-phase trials: phase clauses exclude the neighbouring phases (`AREA[Phase]PHASE2 AND NOT AREA[Phase]PHASE1 AND NOT AREA[Phase]PHASE3`), so a Phase 1/2 trial falls only in the Phase 1/2 bucket. For lung cancer the 9 phase counts sum to 14,719, the exact total. A live test checks this for phase, status, and sponsor class.
- Rate limit: the method is used only when it needs fewer requests than fetching the records (buckets < pages of 1,000), or when the query matches more than `max_records`.

So "lung cancer by phase" costs 10 requests and is exact, where it was a 3,000-record sample before. `meta.count_method` says which method made the counts, and `meta.bucket_clauses` makes each count reproducible.

### D33. Fetch every record up to 20,000 matches; no parallel fetch
2026-11-12. Accepted. The user approved a 20,000 cap. Changes the plan reviewed with the user, which had parallel date-range shards.
Up to `max_records` (20,000) matches, the retriever fetches every record, so the counts are exact for every chart type. Above it, a chart that needs records (names, countries, years, networks, histograms, scatter plots) uses the first `sample_records` (3,000) in relevance order, as before (A05).
We measured the rate limit before building the parallel fetch: about 10 requests in a burst, then about 1 request/s. At 2 requests/s, 35% of requests got HTTP 429, and a 429 takes about 9 s to clear. A page of 1,000 records already takes about 0.9 s, so a sequential fetch is close to the limit. Parallel shards would gain a few seconds from the burst and then hit 429s. The client now has a token bucket (burst 8, 0.8 request/s) in place of the fixed gap, so bursts of count calls do not trigger 429s. At 1.0 request/s, the e2e eval still got 10 retries in 27 cases, so the server's refill is likely a little under 1/s. Cost: a 20,000-record fetch takes about 20 to 25 s, cached for an hour. The e2e p95 latency went from 5.9 s to 18.6 s; that is the price of exact counts on broad record charts (top sponsors, cross-tabs).

### D34. Entity check: count every name, correct likely typos from registry data
2026-11-12. Accepted. Thresholds approved by the user.
A new stage (`resolve.py`) runs after the hints and before the compiler. It counts every drug, condition, and sponsor name in the plan (filters and compared values) on its own. A name with fewer than 20 matches is compared with up to 3 suggestions from the registry's autocomplete (`/api/int/suggest`, dictionaries InterventionName, Condition, LeadSponsorName). The best suggestion replaces the name if it has at least 10 times as many trials (and at least 1). Why the second condition: the registry itself has typos, so a misspelling is rarely at 0. `pembrolizimab` matches 3 trials and `pembrolizumab` 3,006. Code makes the choice from counts; the LLM never proposes names, so it cannot invent one. Every check is in `meta.entities`, so the UI can show "pembrolizumab: 3,006 trials". A healthy name costs one count request, which the retriever's cache shares with the main query.
The suggest endpoint is not in the documented API (the user accepted that). If it fails, the name is kept, and a 0-match name gets a note saying so.

### D35. Auto-correct with a note, not a clarification round-trip
2026-11-12. Accepted. The user chose this over a `needs_clarification` status.
A corrected name is searched at once, the title uses it, and the first note says what changed: `"pembrolizimab" matches only 3 trials; searched for "pembrolizumab" (3,006 trials), the registry's closest drug name, instead.` The UI shows that note above the chart. The response schema keeps two statuses. Cost: a user who meant the rare spelling cannot opt out in this version. A correction that would make two compared values equal is skipped.

### D36. Shared resources are safe under concurrent requests
2026-11-12. Accepted. Fixes a bug: FastAPI runs the sync `/query` endpoint in a thread pool, and the token bucket (D33) had no lock, so concurrent requests could each spend the same tokens and exceed the API limit together.
- The rate limiter takes a lock, so all requests share one budget and wait in order.
- The cache is bounded (`CACHE_MAX_ENTRIES`, 512, least recently used out) and thread-safe.
- Single flight: concurrent requests for the same count or fetch wait for one registry call instead of each sending it. A test runs 6 identical requests at once and sees 2 calls (1 count, 1 fetch).
- Each request has a time budget for registry calls (`REQUEST_BUDGET_S`, 90 s). Past it, the next call raises `BudgetExceeded`, and the API returns HTTP 504 with a hint to narrow the question.
The service stays synchronous. Nothing measured so far needs async; the registry's 1 request/s limit, not the server, is the bottleneck.

### D37. Completeness, sampling, and citations are separate fields
2026-11-12. Accepted. Replaces `records_used` and `truncated`.
One flag mixed three things: how many records were downloaded, how many trials the counts cover, and whether a datum lists all its trials. That made a `total` answer look truncated (it fetches 25 records to cite, but its number is the exact registry count). `meta` now has `total_matching`, `trials_counted`, `count_completeness` (exact or sample), `count_method`, `records_fetched`, `citation_limit`, and `coverage` with the same fields per series. `visualization.subtitle` states completeness on the chart ("Partly sampled, SAMPLE of 3,000 of 124,749 trials"), so a sample cannot be missed. The e2e eval checks that these fields agree with each other.

### D38. Derived values cite every source field; cells cite both dimensions
2026-11-12. Accepted. Completes D15.
Duration used to cite a made-up path (`statusModule`) with a composed excerpt ("startDate X; completionDate Y"), which broke the rule that an excerpt is the exact value at its path. It now cites `startDateStruct.date` and `completionDateStruct.date` separately, and `meta.derivations` states the formula. A cross-tab cell used to cite only its second dimension; it now cites both, like network edges. A test resolves every citation path (with list indexes) on the real fixtures and checks that the excerpt equals the value there.

### D39. Map names to MeSH only on evidence of identity
2026-11-12. Accepted. Supersedes the matching rule in D11.
The old rule matched a name to any MeSH term it contained or was contained in. On the fixtures, that added related formulations and categories as drugs: "Paclitaxel" also counted as "130-nm albumin-bound paclitaxel" in trials with both arms, a biosimilar code in `otherNames` (CT-P10) became a second drug next to rituximab, "Immunoglobulin G" came from bevacizumab's chemical name, and conditions collapsed into "Neoplasms". The new rule (`registry._map_names`):
1. exact: the name's words equal the term's words, after removing doses, routes, dosage forms, brackets, and trademark signs.
2. alias: the term equals words the sponsor gave as the same thing: an `otherNames` entry, a parenthesised or bracketed name, one item of a list name ("A + B", "A in combination with B"), or the same words reordered.
3. Conditions only, broader: the most specific term whose words all appear in the name ("Stage IV Melanoma" -> "Melanoma").
4. Otherwise free text: the cleaned name.
Exact wins over alias, so "CC-486" (otherName "Oral Azacitidine") is one drug, not two. Drug-class terms (last word "inhibitors", "agents", "therapy", ...) are never drug values. Every drug and condition citation records the rule in `match`. On the 400 fixture records, 60 labels changed; each change was reviewed, and the regression tests pin the cases above. Cost: some true synonyms stay separate when the record gives no evidence ("5-fluorouracil" and "Fluorouracil", "Mitomycin C" and "Mitomycin").

### D40. Disclose constraints that no filter can execute
2026-11-12. Implemented under the trustworthiness priorities.
The planner records `ignored_constraints` and `interpretation_notes`. A useful partial registry chart stays supported, with omitted constraints visible in metadata, its subtitle, and the UI. A request whose central answer is unavailable returns `unsupported`. A held-out adversarial set was written before this prompt work and remains outside development runs.

### D41. Verify interpretation, execution, and answer separately
2026-11-12. Implemented under the trustworthiness priorities.
Live end-to-end checks inspect the raw plan, observed cohort fetches, and final chart separately. Six hand-labeled records pin known counts independently of the production extraction logic. The public schema exports typed rows and validates chart channels, integer counts, citation consistency, and network references. Network `top_n` applies per entity group; ignored hints and controls produce notes. Unsupported questions skip registry requests.

### D42. Package a revision with reproducible evidence
2026-11-12. Implemented under the submission priorities.
The ZIP contains tracked project files plus a code revision manifest. Environment secrets, dependencies, caches, and git history are excluded. Validation reports distinguish development from held-out results and saved demo playback from live execution. A fresh planner run exposed a ranking failure that the earlier perfect run missed, so both the result and the limitation are disclosed.

### D43. Static demo on GitHub Pages; no hosted backend
2026-11-12. Agreed with the user.
The public demo is the frontend on GitHub Pages with saved runs only. No backend is deployed, so the OpenAI key never leaves the local machine and no server costs or abuse risk exist. Every question in the "What can I ask?" guide has a saved run (examples 01 to 15), so each button works without a backend. A reviewer who wants live answers runs the project locally with the README steps. Cost: the public page cannot answer typed questions, and saved runs are snapshots of the registry.

## Assumptions

| ID | Assumption | If false |
|---|---|---|
| A01 | `query.intr` and `query.cond` synonym expansion is good enough for entity matching (pembrolizumab, Keytruda, MK-3475 all return 3,006). | Add an entity-resolution step against MeSH. |
| A02 | ~~A 3,000-record cap keeps a response under about 10 seconds.~~ Replaced by D33: a 20,000-record fetch takes about 20 s; registry counts are used where they are cheaper. | Lower `MAX_RECORDS`. |
| A03 | ~~The rate limit allows a few requests per second.~~ False: measured about 1 request/s sustained with a burst of about 10 (D33). The client's token bucket follows that. | Lower `CTGOV_RATE_PER_S`. |
| A04 | Reviewers judge the backend and the JSON spec. The frontend is a bonus. | Spend more time on the UI. |
| A05 | Relevance order (the API default) is a fair sample when a fetch is truncated. Now applies only above 20,000 matches, and only to dimensions without bucket clauses (D32, D33). | Fetch by a neutral sort. |
| A07 | Deadline is 2026-11-13 morning (about 12 hours from 2026-11-12 evening). Scope favors breadth of query classes over polish. | Re-plan milestones. |
| A06 | A model upgrade fixes interpretation errors, not design errors. Evals separate the two. | n/a (this is how we read eval results) |
