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
| `MAX_RECORDS` | `3000` | Most study records fetched per series. |
| `CACHE_TTL_S` | `3600` | In-memory cache lifetime for registry fetches. |
| `CTGOV_MIN_INTERVAL_S` | `0.2` | Minimum gap between ClinicalTrials.gov requests. |

## How it works

```
POST /query (QueryRequest)
  1. Planner (LLM)    question -> QueryPlan (typed, validated; one repair retry)
  2. Hints            structured request fields override the plan's filters
  3. Compiler         QueryPlan -> ClinicalTrials.gov search parameters, one set per series
  4. Fetcher          paged fetch of only the needed fields, capped, cached
  5. Registry         per dimension: record -> normalized values, each with a citation
  6. Aggregator       counts, cross-tabs, networks, histogram bins, scatter points
  7. Chart selector   plan shape -> chart type (fixed rules)
  8. Spec builder     rows, encoding, title, meta notes
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

The structured fields are facts from the caller. They override whatever the LLM read from the question. A field that contradicts the question's own constraints returns 422.

```json
{"query": "How has the number of trials for this drug changed over time?",
 "drug_name": "Pembrolizumab", "start_year": 2015}
```

HTTP status codes: 200 for any answer, including `unsupported`. 422 for an invalid request. 502 when the LLM or ClinicalTrials.gov fails.

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
- `title`: generated from the plan, for example "Trials for multiple sclerosis (recruiting) by country".
- `encoding`: maps channel names to data fields. Each channel is `{field, type, title, sort}`, where `type` is `nominal`, `ordinal`, `quantitative`, or `temporal`.
- `data`: a list of rows, or `{nodes, edges}` for `network_graph`. Rows come in display order.

Every row, node, and edge has:

- `citation_count`: how many trials support this datum. Equal to its count.
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

`<dimension>` is the plan dimension name: `phase`, `overall_status`, `study_type`, `lead_sponsor`, `sponsor_class`, `intervention_type`, `drug`, `condition`, or `country`. Measures are `enrollment` (participants) and `duration_months` (start to completion date).

In a network, node weight is the number of trials that include the entity, and edge weight is the number of trials that include both ends. In a co-occurrence network (drug to drug), an edge means both drugs appear in the same trial.

### `meta`

| Field | Meaning |
|---|---|
| `plan` | The validated `QueryPlan`: how the question was interpreted. |
| `filters` | Filters applied (after hints). |
| `api_query` | Exact ClinicalTrials.gov parameters. With a comparison, one set per series. |
| `source`, `data_timestamp` | `clinicaltrials.gov` and the registry snapshot time. |
| `records_used`, `total_matching`, `truncated` | Records the chart is built from, all matches, and whether the fetch cap cut the data. |
| `multi_valued` | True when one trial can count in several groups (countries, drugs, conditions). |
| `units`, `sort`, `time_granularity` | Rendering hints. |
| `notes` | Plain-language caveats: truncation, groups not shown, trials missing a value, how to read estimated counts. For `unsupported`, the reason. |
| `llm` | Model, prompt version, attempts, and cost in USD of the planner call. |

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

## Testing and evals

```sh
uv run pytest                                   # 188 offline tests, about 2 seconds
uv run pytest -m live                           # real OpenAI and ClinicalTrials.gov calls
uv run python -m evals.planner_eval --runs 3    # 72 golden cases x 3 runs, plan checked by code
uv run python -m evals.e2e_eval                 # full pipeline on the live registry
```

Offline tests run against real API records recorded once into `tests/fixtures/` ([scripts/record_fixtures.py](scripts/record_fixtures.py)), so they are fast, deterministic, and use real data shapes. One test re-reads every cited path from the record and checks that the excerpt equals the value there.

The golden dataset has 27 hand-written cases (every example question in the assignment appendix, the other question classes, edge cases such as prompt injection, and regression cases from failures we saw) and 45 synthetic cases generated from explicit dimensions (intent x entity x filter x phrasing style), where code derives the expected plan and an LLM only writes the question text. Every check is deterministic code. There is no LLM judge.

Latest results (all committed in `evals/results/`):

| Eval | Model | pass^k (k=3) | 95% CI | Cost per success | p50 / p95 latency |
|---|---|---|---|---|---|
| Planner, 72 cases | gpt-5.4-mini | 100% | 94.9 to 100% | $0.00145 | 1.1 / 1.5 s |
| Planner, 72 cases | gpt-5.4-nano | 95.8% | 88.5 to 98.6% | $0.00042 | 1.3 / 2.0 s |
| End-to-end, 27 cases, live | gpt-5.4-mini | 27/27 | 88 to 100% | | 2.7 / 5.9 s |

CI runs lint and the offline tests on every push. A second workflow runs the planner evals on pull requests that touch the prompt, plan schema, or model settings, and fails if any intent drops below its floor in `evals/thresholds.json` (it needs an `OPENAI_API_KEY` repository secret). [docs/03-evals.md](docs/03-evals.md) explains the method, the failure categories, and how each failure we found became a regression case.

## Design decisions and trade-offs

The full log, with the alternatives we rejected and the evidence behind each choice, is in [docs/decisions.md](docs/decisions.md). The main ones:

- The LLM only plans. A typed plan is easy to validate and to evaluate, and code produces every number. A tool-calling agent loop could handle stranger questions, but it is harder to test, and its output numbers could be invented.
- Chart type follows from the plan by fixed rules, so the same plan always gives the same chart.
- We fetch slim records (only the needed fields) and count them in code, instead of asking the API for one count per bucket. This gives one consistent counting method, makes networks possible, and gives citations for every datum. The cost: broad questions (14,000 lung cancer trials) use the first 3,000 records, and `meta` says so. The per-bucket method also has a correctness problem: Phase 1/2 trials match both Phase 1 and Phase 2 searches, so pembrolizumab's per-phase counts sum to 3,295 while it has 3,006 trials.
- Phase 1/2 and Phase 2/3 are their own buckets, matching how the registry records them. Countries, drugs, and conditions can count one trial in several groups, and `meta.multi_valued` flags it.
- Estimated (future) start dates are kept but counted separately in `estimated_count`.
- Drugs and conditions are grouped by NLM MeSH terms from the record's derived section, with the free-text name as fallback. Citations still quote the free-text name exactly as the sponsor entered it.
- The default model, `gpt-5.4-mini`, was chosen by the planner evals. The model is one setting, and four models with the same request shape are supported.

## Limitations and what I would improve

- Sampling: when more than `MAX_RECORDS` trials match, charts use the first ones in the API's relevance order, which may not be a representative sample. With more time: fetch in parallel with date-range partitions, or switch enum dimensions to exact counts and keep sampling only for networks.
- Entity matching depends on the API's synonym search and on MeSH matching. A few synonyms stay separate (`aldesleukin` and `Interleukin-2` in melanoma). Sponsor names are not normalized, so subsidiaries count as separate sponsors. An entity-resolution step against the MeSH API would fix most of this.
- Country counts come from site locations. Some completed trials have their locations removed from the public record, so these counts are lower than the real numbers.
- Duration uses the completion date, which can be an estimate for ongoing trials.
- Comparison series are separate searches, so a trial that mentions both compared drugs counts in both series.
- No results-section analysis (outcomes, adverse events). Efficacy questions are answered as unsupported.
- The cache is in-memory and per process. The ClinicalTrials.gov rate limit is not documented. We saw HTTP 429 under bursts, and the client backs off and retries.
- More dimensions would be cheap to add through the registry: collaborators, investigators, sites, masking, allocation, age groups.
- The planner prompt is tuned on 19 eval cases. A larger and more adversarial eval set would make model and prompt changes safer.

## Use of AI tools

This project was built with Claude Code (Anthropic) as a pair programmer, with OpenAI models as the runtime planner.

- Design: the architecture, the LLM's limited role, the counting strategy, and the schemas were discussed and decided step by step. Each decision is recorded with its reason in [docs/decisions.md](docs/decisions.md). The API behavior behind those decisions was probed directly and is written up in [docs/01-data-source.md](docs/01-data-source.md).
- Implementation: most code was generated by the assistant test-first (tests written before the code they cover) and reviewed in small commits. The git history shows the order.
- Validation: unit tests on recorded real data, live tests against both APIs, planner evals and end-to-end evals with committed results, and manual review of live outputs. That review found and fixed several issues: sponsor names were wrongly capitalized, a word-order mismatch with MeSH condition terms, the comparison planning errors that the first eval run exposed, and a bug that lost meta notes.
