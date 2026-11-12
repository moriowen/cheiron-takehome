# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Take-home assignment: a backend service that turns a natural-language clinical-trials question into a structured visualization spec, backed by the ClinicalTrials.gov Data API (https://clinicaltrials.gov/data-api/api). The spec is in [TASK.md](TASK.md). It is the single source of truth for requirements, deliverables, and grading. Read it before any design decision.

State: no code yet. Language, framework, and layout are not chosen. When they are, add the build, run, and test commands here (including how to run a single test).

## Decision drivers from TASK.md

Grading weights steer trade-offs: system design 35%, AI/agent design 20%, code quality 20%, query/visualization coverage 15%, input/output schema design 10%. Deep citations are a bonus.

- **One coherent pipeline, many query classes.** Coverage must come from a general approach (interpret → retrieve → pick chart → build spec), not per-query special cases. Network graphs score above single-chart systems.
- **Keep the LLM away from numbers.** The LLM may interpret the query and pick a plan from a constrained schema. Counts, aggregates, and chart data must come from API data in code, then be validated. Graders look for low hallucination risk.
- **Every response has** `visualization.{type, title, encoding, data}` plus `meta` (filters, assumptions, units, sorting, granularity). The schema must let a frontend render with no guessing.
- **Deep citations (bonus):** each datum carries `citations: [{nct_id, excerpt}]`, where `excerpt` is an exact field value or text from the API response.

## Deliverables to keep current

The README must document: how to run, the request and response schemas, design decisions and trade-offs, limitations, and the AI-tool usage note (tools used, how correctness was validated, what was designed vs generated). The submission also needs 3–5 example queries with the actual JSON output the system produced, not hand-written output.
