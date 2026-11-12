# Implementation plan

Why each choice was made lives in [decisions.md](decisions.md) (cited as Dnn). This file says what we build and in which order. Update the status column as milestones finish.

## Pipeline

```
POST /query (QueryRequest)
  1. Planner (LLM)      question -> QueryPlan                                  D02, D03
  2. Plan compiler      QueryPlan -> StudyQuery + list of API fields
  3. Fetcher            CTGovClient + cache -> study records (capped)          D07, D08
  4. Field registry     per Dimension: record -> [(value, citation)]           D09-D11, D15
  5. Aggregator         breakdown / trend / network / histogram / scatter tables
  6. Chart selector     plan shape -> visualization type (rules)               D13
  7. Spec builder       table -> visualization + meta                          D12, D14
-> QueryResponse
```

The field registry (stage 4) holds everything about one dimension in one place: the API field path, the normalization, and the citation excerpt. A new question type should need a registry entry, not a new code path.

## Question taxonomy

Each question class maps to one `analysis` kind in the plan. The chart follows from the kind and the plan shape, never from the LLM directly.

| Class | Example (from TASK.md appendix) | analysis | group_by | Chart |
|---|---|---|---|---|
| Time trend | How has the number of trials for [drug] changed per year since 2015? | `trend` | `[start_year]` | `time_series` |
| Trend comparison | Trials per year for drug A vs drug B | `trend` + comparison | `[start_year]` | `time_series` (one series per value) |
| Distribution | How are [condition] trials distributed across phases? | `breakdown` | `[phase]` | `bar_chart` |
| Top N | Most common intervention types for [drug] trials | `breakdown` | `[intervention_type]` | `bar_chart` (horizontal, sorted) |
| Geographic | Which countries have the most recruiting trials for [condition]? | `breakdown` | `[country]` | `bar_chart` (horizontal, top N) |
| Comparison | Compare phases for trials of drug A vs drug B | `breakdown` + comparison | `[phase]` | `grouped_bar_chart` |
| Cross-tab | Phase by status for [condition] | `breakdown` | `[phase, overall_status]` | `grouped_bar_chart` |
| Bipartite network | Network of sponsors and drugs for [condition] | `network` | `[lead_sponsor, drug]` | `network_graph` |
| Co-occurrence network | Which drugs co-occur in combination studies? | `network` | `[drug, drug]` | `network_graph` |
| Numeric distribution | Distribution of enrollment sizes for [condition] trials | `histogram` | `[]`, measure `enrollment` | `histogram` |
| Two measures | Enrollment vs duration for [drug] trials | `scatter` | `[]`, measures `enrollment`, `duration_months` | `scatter_plot` |
| Single number | How many recruiting trials for [condition]? | `total` | `[]` | `metric` |
| Out of scope | Is drug A more effective than drug B? | n/a | n/a | `status: unsupported` |

## Schemas (summary; the code is the source of truth)

`QueryRequest`: `query` (required, 1 to 500 chars) plus optional hints `drug_name`, `condition`, `sponsor`, `country`, `phase`, `status`, `start_year`, `end_year`. Hints are facts from the caller and become filters.

`QueryPlan` (LLM output): `supported`, `unsupported_reason`, `analysis`, `group_by` (0 to 2 dimensions), `measures`, `filters`, `comparison`, `top_n`.

`QueryResponse`:
```
status: "ok" | "unsupported"
visualization: {type, title, encoding, data} | null
meta: {
  plan, filters, api_query, source, data_timestamp,
  records_used, total_matching, truncated, multi_valued,
  units, sort, time_granularity, notes[],
  llm: {model, prompt_version, attempts, cost_usd}
}
```
Every datum (row, node, edge) carries `citations` and `citation_count` (D15). Network `data` is `{nodes: [...], edges: [...]}`. All other types use a list of rows.

## Milestones

Each milestone ends with tests green, lint clean, a commit, and this file and `decisions.md` updated.

| # | Milestone | Done when | Status |
|---|---|---|---|
| M0 | LLM planner layer, ClinicalTrials.gov client | Offline and live tests pass | Done |
| M1 | Final `QueryPlan`, `QueryRequest`, `QueryResponse` models | Every taxonomy row can be written as a valid plan; schema tests pass | |
| M2 | Planner eval set and runner | Each appendix question has an expected plan; `evals/run.py` prints pass rate and cost per model | |
| M3 | Recorded fixtures and field registry | Every Dimension and measure extracts values with citations from real fixtures | |
| M4 | Aggregator | Breakdown, cross-tab, trend, network (bipartite and co-occurrence), histogram, scatter, total all tested on fixtures | |
| M5 | Chart selector and spec builders | Every taxonomy row produces a valid `QueryResponse` from a fixed plan and fixtures | |
| M6 | Pipeline, FastAPI `POST /query`, cache, Docker | End-to-end request works locally and in Docker | |
| M7 | End-to-end evals, example runs, README | 3 to 5 example JSON outputs committed from real runs; README covers every TASK.md item | |
| M8 | Chat frontend with D3, static deploy with presets | Every chart type renders; deployed URL works | |

## Evals

Two levels, both run by one script:

1. Planner evals (cheap, run often): question -> expected plan fields. Checks analysis kind, dimensions, filters, comparison, and supported. Free-text values are compared case-insensitively. Reports pass rate, failures, tokens, and cost per model.
2. End-to-end evals (slower, hit the live API): question -> response invariants. Checks chart type, non-empty data, every datum has citations, citation NCT IDs exist in the fetched records, counts are non-negative, and `meta` is complete.

Each failure is tagged as interpretation (the model misread the question) or design (the plan schema or pipeline cannot express it) (A06).
