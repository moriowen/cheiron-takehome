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
2026-11-12. Accepted.
We fetch matching studies with a `fields=` projection up to a cap (default 3,000) and aggregate in code. `meta` reports records used, total matches, and whether the fetch was truncated. Why: it gives one consistent counting method for every chart type, it is the only way to build networks, and it gives citations for every datum. Rejected: one exact `count()` call per bucket. It cannot produce citations, it costs many calls against an undocumented rate limit, and multi-phase trials make bucket counts disagree with the total (pembrolizumab per-phase counts sum to 3,295 while the total is 3,006).

### D09. Multi-valued fields
2026-11-12. Accepted.
Phase uses combined buckets ("Phase 1/2"), because the registry records them as one design. Countries, drugs, conditions, and sponsors count a trial once in each bucket it belongs to. `meta.multi_valued` is true and a note says bars can sum to more than the trial count.

### D10. Estimated dates are kept and marked
2026-11-12. Accepted.
Start dates can be `ESTIMATED` and in the future. Time buckets carry `actual` and `estimated` counts so a renderer can draw them differently. Years are the only time granularity, because 35% of dates have month precision only.

### D11. Drug identity is the intervention MeSH term
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

## Assumptions

| ID | Assumption | If false |
|---|---|---|
| A01 | `query.intr` and `query.cond` synonym expansion is good enough for entity matching (pembrolizumab, Keytruda, MK-3475 all return 3,006). | Add an entity-resolution step against MeSH. |
| A02 | A 3,000-record cap keeps a response under about 10 seconds. A 1,000-record page took about 1 second. | Lower the cap or add the cache earlier. |
| A03 | The ClinicalTrials.gov rate limit allows a few requests per second from one client. | Raise `min_interval_s`, add caching. |
| A04 | Reviewers judge the backend and the JSON spec. The frontend is a bonus. | Spend more time on the UI. |
| A05 | Relevance order (the API default) is a fair sample when a fetch is truncated. | Fetch by a neutral sort, or report counts only from exact totals. |
| A07 | Deadline is 2026-11-13 morning (about 12 hours from 2026-11-12 evening). Scope favors breadth of query classes over polish. | Re-plan milestones. |
| A06 | A model upgrade fixes interpretation errors, not design errors. Evals separate the two. | n/a (this is how we read eval results) |
