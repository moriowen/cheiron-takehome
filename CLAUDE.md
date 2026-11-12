# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Take-home assignment: a backend service that turns a natural-language clinical-trials question into a structured visualization spec, backed by the ClinicalTrials.gov Data API (https://clinicaltrials.gov/data-api/api). The spec is in [TASK.md](TASK.md). It is the single source of truth for requirements, deliverables, and grading. Read it before any design decision.

Stack: Python 3.12, uv, Pydantic v2, OpenAI SDK. FastAPI service and Dockerfile come later.

## Commands

```sh
uv sync                                   # install
uv run pytest                             # offline tests (default excludes `live`)
uv run pytest tests/test_plan.py::test_valid_plan_parses   # one test
uv run pytest -m live                     # real OpenAI calls, costs < $0.01
uv run ruff check . && uv run ruff format .
```

## Architecture (so far)

The LLM produces only a `QueryPlan` (`src/trialviz/plan.py`). Code does fetching, counting, chart choice, and citations. Callers depend on the `Planner` protocol (`llm/planner.py`). `OpenAIPlanner` is the only module that imports `openai`. `StubPlanner` serves tests. A plan that fails Pydantic validation gets one repair retry with the errors fed back. Each call logs tokens and cost (`llm/usage.py`). Bump `PROMPT_VERSION` in `llm/prompt.py` on every prompt change. `QueryPlan` and `QueryRequest` are provisional until the question taxonomy is settled.

Gotcha: openai SDK 3.x depends on `httpx2`, not `httpx`.

## LLM access

Provider is OpenAI only. The key is `OPENAI_API_KEY` in `.env` (git-ignored; template in `.env.example`). Never read, print, or commit `.env`. A local `.git/hooks/pre-commit` blocks staged `.env` files and `sk-...` strings.

Allowed models (the key works only for these): `gpt-4o-mini`, `gpt-4o-2024-08-06`, `gpt-4.1-nano`, `gpt-4.1-mini`, `gpt-4.1`, `gpt-5-mini`, `gpt-5`, `gpt-5.1`, `gpt-5.2`, `gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-5.4`. `gpt-4.1-nano` is on the list but returns 404 for this key (checked 2026-11-12). The key cannot read billing or usage, so log token usage per call to track spend. Keep the model name in config, not in code, so evals can compare models.

Dev model is `gpt-5.4-nano`. Swap targets are `gpt-5.4-mini`, `gpt-5.4`, and `gpt-5.1`. Send only the parameters that all four accept: `max_completion_tokens` (the API rejects `max_tokens` on the 5.x models), `temperature=0`, `reasoning_effort="none"`, and `response_format` with a strict `json_schema`. `gpt-5` and `gpt-5-mini` reject `temperature=0` and `reasoning_effort="none"`, so they need a different parameter set. Do not use them.

API exploration notes: [docs/01-data-source.md](docs/01-data-source.md).

## Decision drivers from TASK.md

Grading weights steer trade-offs: system design 35%, AI/agent design 20%, code quality 20%, query/visualization coverage 15%, input/output schema design 10%. Deep citations are a bonus.

- **One coherent pipeline, many query classes.** Coverage must come from a general approach (interpret → retrieve → pick chart → build spec), not per-query special cases. Network graphs score above single-chart systems.
- **Keep the LLM away from numbers.** The LLM may interpret the query and pick a plan from a constrained schema. Counts, aggregates, and chart data must come from API data in code, then be validated. Graders look for low hallucination risk.
- **Every response has** `visualization.{type, title, encoding, data}` plus `meta` (filters, assumptions, units, sorting, granularity). The schema must let a frontend render with no guessing.
- **Deep citations (bonus):** each datum carries `citations: [{nct_id, excerpt}]`, where `excerpt` is an exact field value or text from the API response.

## Deliverables to keep current

The README must document: how to run, the request and response schemas, design decisions and trade-offs, limitations, and the AI-tool usage note (tools used, how correctness was validated, what was designed vs generated). The submission also needs 3–5 example queries with the actual JSON output the system produced, not hand-written output.
