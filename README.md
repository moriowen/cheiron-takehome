# Clinical trials query-to-visualization API

A backend that turns a question about clinical trials into a visualization spec built from [ClinicalTrials.gov](https://clinicaltrials.gov) data. Every bar, time bucket, histogram bin, scatter point, network node, and network edge cites the trial records behind it, with the exact field value from the API.

```
POST /query  {"query": "Show a network of sponsors and drugs for melanoma trials."}
->  {"status": "ok", "visualization": {"type": "network_graph", ...}, "meta": {...}}
```

Supported chart types: `bar_chart`, `grouped_bar_chart`, `time_series`, `histogram`, `scatter_plot`, `network_graph` (bipartite and co-occurrence), and `metric`. Questions the registry cannot answer get a structured `unsupported` response instead of a forced chart.

## Contents

- [Demo](#demo)
- [How to run](#how-to-run)
- [How it works](#how-it-works)
- [Request schema](#request-schema)
- [Response schema](#response-schema)
- [Example runs](#example-runs)
- [Testing and evals](#testing-and-evals)
- [Design decisions and trade-offs](#design-decisions-and-trade-offs)
- [Limitations and what I would improve](#limitations-and-what-i-would-improve)
- [Use of AI tools](#use-of-ai-tools)

## Demo

A static demo of the chat frontend replays the example runs below (real outputs, no backend needed): **https://moriowen.github.io/cheiron-takehome/frontend/**. Click any bar, point, node, or edge to see the trials and exact registry values behind it. With the backend running, the same page at `http://localhost:8000/` answers new questions live.

## How to run

You need an OpenAI API key. ClinicalTrials.gov needs no key.

```sh
cp .env.example .env          # then set OPENAI_API_KEY=sk-...
```

With Docker:

```sh
docker compose up --build     # API on http://localhost:8000, docs at /docs
```

Without Docker (needs [uv](https://docs.astral.sh/uv/)):

```sh
uv sync
uv run uvicorn --factory trialviz.api:build_app --reload --port 8000
```

Try it:

```sh
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"query": "How are breast cancer trials distributed across phases?"}' | jq .visualization.title
```

Interactive API docs with the full JSON schemas are at `http://localhost:8000/docs`.

Optional settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_MODEL` | `gpt-5.4-mini` | Planner model. Also accepted: `gpt-5.4-nano`, `gpt-5.4`, `gpt-5.1`. |
| `MAX_RECORDS` | `20000` | Up to this many matches, every record is fetched and counts are exact. |
| `SAMPLE_RECORDS` | `3000` | Above `MAX_RECORDS`, charts that need records use this many, in relevance order. |
| `CACHE_TTL_S` | `3600` | In-memory cache lifetime for registry fetches. |
| `CACHE_MAX_ENTRIES` | `512` | Cache size bound; least recently used entries are evicted. |
| `REQUEST_BUDGET_S` | `90` | Registry time per request; past it the API returns HTTP 504. |
| `CTGOV_RATE_PER_S`, `CTGOV_BURST` | `0.8`, `8` | Token bucket for ClinicalTrials.gov requests (the measured limit). |

## How it works

```
POST /query (QueryRequest)
  1. Planner (LLM)    question -> QueryPlan (typed, validated; one repair retry)
  2. Hints            structured request fields override the plan's filters
  3. Entity check     count each name; replace a likely typo by the registry's suggestion
  4. Compiler         QueryPlan -> ClinicalTrials.gov search parameters, one set per series
  5. Retriever        exact counts per bucket, or every record (sample above 20k), cached
  6. Registry         per dimension: record -> normalized values, each with a citation
  7. Aggregator       counts, cross-tabs, networks, histogram bins, scatter points
  8. Chart selector   plan shape -> chart type (fixed rules)
  9. Spec builder     rows, encoding, title, meta notes
-> QueryResponse
```

The LLM does one job: it reads the question and fills in a `QueryPlan`. The plan says what kind of answer is needed (`breakdown`, `trend`, `network`, `histogram`, `scatter`, or `total`), what to group by, which filters apply, and whether values are compared. OpenAI Structured Outputs force the plan into a strict JSON schema, and Pydantic validators then check rules a schema cannot express (a trend groups by start year, a comparison field is not also a filter, and so on). If validation fails, the errors go back to the model once.

The LLM never sees registry data and never writes a number, a chart type, or a title. Code does the counting, so every number in a response comes from the fetched records or from the API's `totalCount`.

All knowledge about a dimension sits in one place, [src/trialviz/registry.py](src/trialviz/registry.py): which API fields it needs, how to normalize values (drug names map to MeSH terms, so `Keytruda®`, `MK-3475`, and `pembrolizumab` count as one drug), and what to cite. Supporting a new dimension means adding one registry entry.

Code map:

| Module | Role |
|---|---|
| `llm/` | `Planner` protocol, OpenAI implementation, prompt, stub for tests, token cost tracking |
| `plan.py` | `QueryPlan` and its validation rules |
| `compiler.py` | Plan to API queries |
| `ctgov/client.py` | ClinicalTrials.gov API v2 client: paging, retries, throttling |
| `registry.py` | Dimensions and measures: fields, extraction, normalization, citations |
| `aggregate.py` | Chart-agnostic tables |
| `builder.py` | Chart selection and spec building |
| `pipeline.py`, `api.py` | Wiring and HTTP |

## Request schema

`POST /query` with a JSON body. Unknown fields are rejected.

| Field | Type | Required | Validation | Meaning |
|---|---|---|---|---|
| `query` | string | yes | 1 to 500 chars | The question. |
| `drug_name` | string | no | max 200 chars | Drug or intervention. |
| `condition` | string | no | max 200 chars | Disease or condition. |
| `sponsor` | string | no | max 200 chars | Sponsor organization. |
| `country` | string | no | max 100 chars | Country with trial sites. |
| `phase` | list of enum | no | `EARLY_PHASE1`, `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4`, `NA` | Only these phases. |
| `status` | list of enum | no | `RECRUITING`, `COMPLETED`, `ACTIVE_NOT_RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `SUSPENDED`, `TERMINATED`, `WITHDRAWN`, `UNKNOWN` | Only these statuses. |
| `start_year` | int | no | 1900 to 2100, at most `end_year` | Earliest start year. |
| `end_year` | int | no | 1900 to 2100 | Latest start year. |

Structured fields replace the corresponding text-derived filters. A field being compared is kept as a comparison instead; the hint is ignored with a note. Overrides that make the plan invalid (for example, a start year later than the end year) return 422. Other conflicts use the structured value.

```json
{"query": "How has the number of trials for this drug changed over time?",
 "drug_name": "Pembrolizumab", "start_year": 2015}
```

HTTP status codes: 200 for any answer, including `unsupported`. 422 for an invalid request. 502 when the LLM or ClinicalTrials.gov fails. 504 when registry work exceeds `REQUEST_BUDGET_S` (the planner has a separate timeout).

## Response schema

```
{
  "status": "ok" | "unsupported",
  "visualization": {type, title, subtitle, encoding, data} | null,
  "meta": {...}
}
```

### `visualization`

- `type`: one of the chart types below.
- `subtitle`: how complete the counts are, for example "Exact counts, all 14,719 trials" or "Partly sampled, SAMPLE of 3,000 of 124,749 trials".
- `title`: generated from the plan, for example "Trials for multiple sclerosis (recruiting) by country".
- `encoding`: maps channel names to data fields. Each channel is `{field, type, title, sort}`, where `type` is `nominal`, `ordinal`, `quantitative`, or `temporal`.
- `data`: a list of rows, or `{nodes, edges}` for `network_graph`. Rows come in display order.

The API validates typed rows, required chart channels, nonnegative integer counts, citation counts, and network references before returning a response. Network `top_n` limits the most frequent entities per group before unlinked nodes are removed (defaults: 15 for bipartite graphs, 25 for co-occurrence graphs). On a chart without ranked groups, `top_n` is ignored with a note.

Every row, node, and edge has:

- `citation_count`: how many trials support this datum. Equal to its count, or 1 for a scatter point.
- `citations`: up to 5 trials, each `{nct_id, field, excerpt, title}`. `field` is the JSON path in the API study record and `excerpt` is the exact value at that path. Trial page: `https://clinicaltrials.gov/study/{nct_id}`.

Channels and row fields per type:

| `type` | Channels | Row fields |
|---|---|---|
| `bar_chart` | `x`, `y`. If `x` is quantitative, draw horizontal bars (used for long labels). | `<dimension>`, `trial_count` |
| `grouped_bar_chart` | `x`, `y`, `series` | `<dimension>`, `series`, `trial_count`. Every series has a row for every category (zeros included). |
| `time_series` | `x` (temporal), `y`, optional `series` | `year`, `trial_count`, `estimated_count`, optional `series`. Years without trials are present with 0. |
| `histogram` | `x` (bin start), `x2` (bin end), `y` | `bin_start`, `bin_end` (null = open-ended), `trial_count`. Bins are `[bin_start, bin_end)`. |
| `scatter_plot` | `x`, `y`, `tooltip` | `nct_id`, `title`, one field per measure. One point per trial. |
| `network_graph` | `node_id`, `node_label`, `node_color`, `node_size`, `edge_width` | Nodes: `id`, `label`, `group` (dimension), `weight`. Edges: `source`, `target`, `weight`. |
| `metric` | `value`, `label` | `label`, `trial_count` |

`<dimension>` is the plan dimension name: `phase`, `overall_status`, `study_type`, `lead_sponsor`, `sponsor_class`, `intervention_type`, `drug`, `condition`, or `country`. Measures are `enrollment` (participants) and `duration_months` (start to completion date). Duration is derived, so it cites both dates at their own paths, and `meta.derivations` gives the formula.

Citation rules: every `citations` entry is `{nct_id, field, excerpt, title}`, where `excerpt` is exactly the value at `field` in that trial's API record (a test checks this for every dimension and measure on more than 1,000 real citations). A missing value is cited as `(field absent)`. A value derived from several fields cites each of them. A cross-tab cell or a network edge cites the values for both of its dimensions.

In a network, node weight is the number of trials that include the entity, and edge weight is the number of trials that include both ends. In a co-occurrence network (drug to drug), an edge means both drugs appear in the same trial.

### `meta`

| Field | Meaning |
|---|---|
| `plan` | The validated `QueryPlan`: how the question was interpreted. |
| `filters` | Filters applied (after hints). |
| `api_query` | Exact ClinicalTrials.gov parameters. With a comparison, one set per series. |
| `source`, `data_timestamp` | `clinicaltrials.gov` and the registry snapshot time. |
| `total_matching` | All registry matches for the query (summed over series). |
| `trials_counted`, `count_completeness` | Trials the counts cover, and `exact` (all of `total_matching`) or `sample` (only `trials_counted` of them). A sample is also stated in `visualization.subtitle`. |
| `count_method` | `registry_count` (one exact count query per bucket), `all_records` (every matching record counted), or `sample` (the first `SAMPLE_RECORDS` only). |
| `records_fetched` | Records downloaded. Smaller than `trials_counted` when counts come from the registry and records only supply citations. |
| `citation_limit` | Most trials cited per datum (5). Each datum's `citation_count` is the full number of supporting trials. |
| `coverage` | The fields above per series, so a comparison shows which series is exact and which is sampled. |
| `bucket_clauses` | With `registry_count`: the Essie clause added to `api_query` for each bucket, so every count can be reproduced. |
| `multi_valued` | True when one trial can count in several groups (countries, drugs, conditions). |
| `units`, `sort`, `time_granularity` | Rendering hints. |
| `entities` | Each drug, condition, or sponsor name in the plan: as asked, its registry matches, the name searched, and whether it was corrected (with the note). |
| `derivations` | For derived measures: how the value is computed from the cited fields. |
| `ignored_constraints` | Requested conditions no filter can execute. They appear above the chart and in its subtitle; the chart covers a wider cohort. |
| `notes` | Plain-language caveats: truncation, groups not shown, trials missing a value, how to read estimated counts. For `unsupported`, the reason. |
| `llm` | Model, prompt version, attempts, and cost in USD of the planner call. |

Constraints such as randomized, adult-only, and double-blind are disclosed as omitted rather than silently applied. The system returns a partial answer when a registry chart is still useful, and `unsupported` for questions about efficacy, safety, prices, or unavailable outcome values. `meta.plan.interpretation_notes` records other reading choices.

## Example runs

Actual outputs from the system, generated by `uv run python scripts/run_examples.py` against the live API:

| File | Question | Result |
|---|---|---|
| [01-trend-with-hint.json](examples/01-trend-with-hint.json) | "How has the number of trials for this drug changed over time?" + `drug_name: Pembrolizumab`, `start_year: 2015` | `time_series` |
| [02-compare-phases.json](examples/02-compare-phases.json) | "Compare phases for trials involving nivolumab vs pembrolizumab." | `grouped_bar_chart` |
| [03-countries-recruiting.json](examples/03-countries-recruiting.json) | "Which countries have the most recruiting trials for multiple sclerosis?" | `bar_chart` |
| [04-sponsor-drug-network.json](examples/04-sponsor-drug-network.json) | "Show a network of sponsors and drugs for melanoma trials." | `network_graph` (bipartite) |
| [05-drug-cooccurrence-network.json](examples/05-drug-cooccurrence-network.json) | "Which drugs frequently co-occur in combination studies for lung cancer?" | `network_graph` (co-occurrence) |
| [06-enrollment-histogram.json](examples/06-enrollment-histogram.json) | "What is the distribution of enrollment sizes in phase 3 asthma trials?" | `histogram` |
| [07-out-of-scope.json](examples/07-out-of-scope.json) | "Is pembrolizumab more effective than nivolumab?" | `unsupported` |
| [08-exact-phase-counts.json](examples/08-exact-phase-counts.json) | "How are lung cancer trials distributed across phases?" | `bar_chart`, exact registry counts for 14,719 trials (`count_method: registry_count`) |
| [09-misspelled-drug.json](examples/09-misspelled-drug.json) | "How are pembrolizimab trials distributed across phases?" | `bar_chart` for "pembrolizumab": the typo matched 3 trials, the fix 3,006 (`meta.entities`) |

## Testing and evals

```sh
uv run pytest                                   # 253 offline tests, about 2 seconds
uv run pytest -m live                           # real OpenAI and ClinicalTrials.gov calls
uv run python -m evals.planner_eval --runs 3    # 79 development cases x 3 runs, plan checked by code
uv run python -m evals.e2e_eval                 # full pipeline on the live registry
```

Offline tests include six manually labeled records with known cohort, comparison, country, year, and network counts in `tests/test_cohort_correctness.py`. Most tests run against real API records recorded once into `tests/fixtures/` ([scripts/record_fixtures.py](scripts/record_fixtures.py)), so they are fast, deterministic, and use real data shapes. One test re-reads every cited path from the record and checks that the excerpt equals the value there.

The development dataset has 34 hand-written cases (every example question in the assignment appendix, the other question classes, edge cases such as prompt injection and misspelled names, and regression cases from failures we saw) and 45 synthetic cases generated from explicit dimensions (intent x entity x filter x phrasing style), where code derives the expected plan and an LLM only writes the question text. Every check is deterministic code. There is no LLM judge.

Earlier results (the latest validation is described below and in `docs/03-evals.md`):

| Eval | Model | pass^k (k=3) | 95% CI | Cost per success | p50 / p95 latency |
|---|---|---|---|---|---|
| Planner, 74 cases | gpt-5.4-mini | 100% | 95.1 to 100% | $0.00145 | 1.0 / 1.3 s |
| Planner, 72 cases | gpt-5.4-nano | 95.8% | 88.5 to 98.6% | $0.00042 | 1.3 / 2.0 s |
| End-to-end, 29 cases, live | gpt-5.4-mini | 29/29 | 88 to 100% | | 5.9 / 22.3 s |

Fresh validation on prompt `2026-11-12.7`: 34/34 development end-to-end cases passed. The planner passed 78/79 development cases on all three runs (236/237 attempts), and its per-intent gate passed. The separate adversarial set passed 14/15 cases for both planner and end-to-end checks. Forecasting was incorrectly accepted; this remains a known limitation. See [docs/submission-validation.md](docs/submission-validation.md) for the evidence and clean-ZIP startup checks.

GitHub CI runs lint and the offline tests on every push. The planner eval gate runs locally with the key in `.env`: `uv run python -m evals.planner_eval --runs 3 --gate` fails if any intent drops below its floor in `evals/thresholds.json`. Run it before committing a change to the prompt, plan schema, or model settings. [docs/03-evals.md](docs/03-evals.md) explains the method, the failure categories, and how each failure we found became a regression case.

## Design decisions and trade-offs

The full log, with the alternatives we rejected and the evidence behind each choice, is in [docs/decisions.md](docs/decisions.md). The main ones:

- The LLM only plans. A typed plan is easy to validate and to evaluate, and code produces every number. A tool-calling agent loop could handle stranger questions, but it is harder to test, and its output numbers could be invented.
- Chart type follows from the plan by fixed rules, so the same plan always gives the same chart.
- Counts are exact wherever the API's rate limit (about 1 request/s, measured) allows it. The retriever picks the method that needs the fewest requests:
  - Phase, status, study type, sponsor class, and intervention type have a fixed set of values, so the registry can count each one. Each count query also returns 5 records, which become that bar's citations. Phase clauses exclude neighbouring phases, so a Phase 1/2 trial is counted once; for lung cancer the 9 phase counts add up to the 14,719 total. "Lung cancer by phase" takes 10 requests.
  - Everything else (sponsors, drugs, countries, years, networks, distributions) is counted from records in code. Up to 20,000 matches, every record is fetched (one request per 1,000).
  - Above 20,000 matches, those charts use the first 3,000 records in the API's relevance order, and `meta` says so.
- Phase 1/2 and Phase 2/3 are their own buckets, matching how the registry records them. Countries, drugs, and conditions can count one trial in several groups, and `meta.multi_valued` flags it.
- Estimated (future) start dates are kept but counted separately in `estimated_count`.
- Names are checked against the registry before the search. Each drug, condition, and sponsor is counted. A name with fewer than 20 trials is compared with the registry's autocomplete suggestions, and replaced when a suggestion has at least 10 times as many trials. "pembrolizimab" (3 trials, typos exist in the registry too) becomes "pembrolizumab" (3,006), and the first note says so. The decision comes from registry counts, not from the LLM.
- Drugs and conditions are grouped by NLM MeSH terms from the record's derived section, but only on evidence of identity: the same words (doses, routes, and brackets removed), or a name the sponsor gave as the same thing (`otherNames`, a parenthesised name, one item of a list such as "A + B"). A substring is not evidence, so "Nab-paclitaxel" stays separate from "Paclitaxel", and a biosimilar listed in `otherNames` does not become an extra drug. Drug classes ("Immune Checkpoint Inhibitors") are never drug values. Without evidence, the free-text name is the label. Each citation's `match` says which rule applied (`exact`, `alias`, `broader` for a condition under a wider term, or `free_text`), and citations still quote the name exactly as the sponsor entered it.
- The default model, `gpt-5.4-mini`, was chosen by the planner evals. The model is one setting, and four models with the same request shape are supported.

## Limitations and what I would improve

- Sampling: above 20,000 matches (for example "cancer", 124,749 trials), charts built from records use the first 3,000 in relevance order, which may not be representative. Fixed-value breakdowns stay exact at any size. A bigger cap costs about 1 s per 1,000 records, because of the rate limit. Parallel fetching does not help: the API allows a burst of about 10 requests and then about 1 per second.
- A fetch of close to 20,000 records takes about 20 s the first time (then it is cached for an hour).
- Entity matching depends on the API's synonym search and on MeSH matching. A few synonyms stay separate (`aldesleukin` and `Interleukin-2` in melanoma). Sponsor names are not normalized, so subsidiaries count as separate sponsors.
- Spelling fixes use the website's autocomplete endpoint, which is not part of the documented API and could change. If it fails, names are searched as written. A user who really meant a rare spelling cannot turn the fix off yet.
- Country counts come from site locations. Some completed trials have their locations removed from the public record, so these counts are lower than the real numbers.
- Duration uses the completion date, which can be an estimate for ongoing trials.
- Comparison series are separate searches, so a trial that mentions both compared drugs counts in both series.
- No results-section analysis (outcomes, adverse events). Efficacy questions are answered as unsupported.
- The cache is in-memory and per process. The ClinicalTrials.gov rate limit is not documented; the client's token bucket uses the limit we measured, and it still backs off and retries on HTTP 429.
- More dimensions would be cheap to add through the registry: collaborators, investigators, sites, masking, allocation, age groups.
- The prompt is developed against 79 cases. A separate 15-case adversarial set is scored only with `--heldout` and is not used to tune the prompt. A repeat development run can still fail, even at temperature 0; saved scores do not guarantee future answers.

## Use of AI tools

This project was built with Claude Code (Anthropic) and Codex (OpenAI) as pair programmers, with OpenAI models as the runtime planner.

- Design: the architecture, the LLM's limited role, the counting strategy, and the schemas were discussed and decided step by step. Each decision is recorded with its reason in [docs/decisions.md](docs/decisions.md). The API behavior behind those decisions was probed directly and is written up in [docs/01-data-source.md](docs/01-data-source.md).
- Implementation: most code was generated by the assistant test-first (tests written before the code they cover) and reviewed in small commits. The git history shows the order.
- Validation: unit tests on recorded real data, live tests against both APIs, planner evals and end-to-end evals with committed results, and manual review of live outputs. That review found and fixed several issues: sponsor names were wrongly capitalized, a word-order mismatch with MeSH condition terms, the comparison planning errors that the first eval run exposed, and a bug that lost meta notes.

## Reviewer path and submission ZIP

1. Configure `.env` and start either Docker or uv using the commands above.
2. Open `/docs` to inspect the request and response schemas. Open `/ui/` for live queries.
3. Try examples 01 (trend), 02 (comparison), 04 (network), 06 (histogram), and 07 (unsupported). Each saved file includes its actual request and response. Saved playback is a snapshot, not a fresh registry query.
4. Run `uv run pytest` and `uv run ruff check .`. For paid live checks, run the planner and end-to-end commands, adding `--heldout` to score the separate adversarial set.
5. See [docs/submission-validation.md](docs/submission-validation.md) for validation results and a concrete failure, diagnosis, fix, and regression test.

After committing changes, `uv run python scripts/package_submission.py` writes `dist/trialviz-submission.zip`. It includes a revision manifest and excludes `.env`, git history, dependencies, caches, and the private assignment.
