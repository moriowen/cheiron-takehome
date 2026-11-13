# API discoverability and demo implementation plan

Status: implemented (commits `bed9684` to `0605541`). Offline tests, lint, JS syntax checks, a stub-DOM inspector check, and the clean-ZIP smoke check (uv and Docker Compose) ran. A manual browser check of the deployed page is still to do.

## Goal and scope

Make the backend contract easy to inspect and give the existing demo a more polished presentation. Keep the Python backend responsible for producing the visualization specification and the browser responsible for drawing it. Keep the current static HTML, JavaScript, and D3 stack.

One business endpoint, `POST /query`, satisfies the assignment. `/health`, `/docs`, and `/openapi.json` support operation and integration. Additional business endpoints, authentication, persistence, and new chart types are outside this pass. A capabilities endpoint can be considered later if an external client needs machine-readable discovery.

## Current state

- The backend already returns `visualization.type`, `title`, `encoding`, and `data`, plus metadata. Python selects the chart and builds its specification; `frontend/render.js` renders it with D3.
- The README includes setup, configuration, request and response schemas, design tradeoffs, limitations, actual example outputs, and AI-tool usage. It also describes newer validation work. Those validation claims are documented evidence, not newly rerun checks for this plan.
- All appendix question families appear in the planner cases: time trends, phase and intervention distributions, drug and condition comparisons, geography, sponsor/drug networks, and drug co-occurrence.
- Additional supported classes include cross-tabs, trend comparisons, enrollment and duration histograms, enrollment-versus-duration scatter plots, ranked entities, and scalar totals. Coverage is bounded by the plan schema; this is not arbitrary clinical question answering.
- The UI has Ask and How it works tabs. It exposes the interpreted plan and compiled registry parameters, but not the complete service response in a dedicated inspector.

## Product changes

### 1. Full API response inspector

Add a top-level API response tab beside Ask and How it works. It must show the exact selected request and complete response, including visualization data, citations, metadata, unsupported responses, and errors. Do not substitute `meta.api_query`, which describes the downstream government query, for the service response.

Include copy request, copy response, download response JSON, and copy curl controls. Show the method and endpoint. For live calls, show the actual HTTP status and elapsed time; label elapsed time as client-observed latency. For saved examples, label the content as saved playback and do not invent live HTTP status or timing. A selected-result control, or View API response action on each answer, must keep the chart and inspected response associated.

Display JSON as text, never executable HTML. Keep the underlying response unchanged. Handle non-JSON error bodies visibly. A rendering failure must not prevent response inspection. Expensive network charts should not be redrawn just to switch tabs.

### 2. Clear question discovery

Add a compact What can I ask? guide near the query composer, with categories and concrete example questions. Explain supported filters and the distinction between example playback and live queries. Link developers to `/docs` only when the backend is available, and to the README in static mode.

Explicitly state boundaries: yearly start-date trends, registration data rather than efficacy/safety conclusions, omitted constraints, sampled broad searches, and trial-level co-occurrence rather than proof of combined treatment. Do not promise forecasting, arbitrary granularity, or unsupported filters.

Use existing saved runs for replay buttons. Text-only examples for classes without saved outputs must not pretend to have replayable results. Do not add a capabilities endpoint just to support these hints.

### 3. Restrained visual refresh

Use a clear research-tool layout: a compact product header, visible Live or Saved demo badge, consistent navigation, a readable question composer, grouped examples, and spacious result surfaces. Improve typography, spacing, chart framing, button hierarchy, and source-panel readability. Use a warm neutral background, a restrained blue or teal accent, and consistent light/dark theme tokens.

Give API JSON a readable monospace surface with horizontal scrolling and copy controls. Preserve prominent warnings about sampling, omitted constraints, and corrected entities. Include useful initial, loading, empty, error, and unsupported states. Keep navigation keyboard accessible, focus indicators visible, and controls usable on narrow screens. Avoid decorative animation, new frontend frameworks, and unnecessary dependencies.

### 4. Documentation and coverage audit

Make the backend/frontend division explicit in the README. Add a concise list of endpoints, a question-type guide separating appendix coverage from extensions, and instructions for using the API response inspector. Check documented field names and error codes against the current models and routes.

Correct stale explanations in How it works. In particular, hints do not all produce 422 on conflict, some non-total analyses use registry bucket counts, and dataset sizes should match current evidence or be omitted in favor of links. Inspect other hardcoded explanatory claims against current code. Preserve honest limitations and distinguish saved results from fresh queries.

## Parallel work allocation

The coordinator first agrees on tab IDs, inspector element IDs, and the result handoff below. Then three agents can work concurrently with exclusive file ownership.

### Agent A: response capture and interaction

Own `frontend/app.js`, `frontend/how.js`, and new `frontend/inspect.js` and `frontend/navigation.js` if useful. Do not edit HTML, CSS, renderers, or README.

Capture request and response once per operation. Hand the inspector an in-memory result with an identifier, the request body, the parsed response when available, the raw response body, live HTTP status and elapsed milliseconds when available, and mode (`live` or `saved`). Store raw live response text before parsing; serialize saved responses without changing their contents. Publish errors before throwing or attempting chart rendering. Keep retained results bounded.

Generalize the existing two-tab navigation for three tabs, including URL hashes, keyboard controls, and preservation of chat state. Implement selection, copy/download behavior, and curl generation. Use element IDs agreed with Agent B. Report any required script tags to Agent B rather than editing its files.

Done when a user can inspect the selected successful, unsupported, or failed operation without making a second API call. Switching tabs must preserve the question, chart, response selection, and citation state.

### Agent B: visual shell and chart presentation

Own `frontend/index.html`, `frontend/style.css`, and presentation-only changes to `frontend/render.js`. Do not edit request handling or navigation JavaScript.

Create the three-tab shell and inspector controls using the agreed IDs, add the new script references, improve the layout and styling, and add the question guide using existing supported examples. Preserve chart click targets and citation access. Do not alter backend-derived counts or recalculate data in the browser. Coordinate example button behavior with Agent A through the existing hooks.

Done when all views have consistent typography and spacing, responsive layout, visible state indicators, and accessible controls. Existing chart families and the source panel must remain usable.

### Agent C: contract and documentation

Own `README.md` and new `docs/query-coverage.md` if the coverage guide needs more space. Do not edit frontend files or backend behavior.

Audit each assignment deliverable against actual files, routes, schema models, examples, and eval cases. Document supported question classes, extensions, limitations, endpoints, and the backend/frontend responsibility boundary. Provide corrected copy for How it works to Agent B. Mark the new UI features as pending until integration is verified; then update their usage instructions.

Done when the README accurately describes the final implementation and a reviewer can find a supported question, send it to the API, inspect the response, and understand how a renderer consumes it.

## Integration and verification

1. Coordinator checks ownership boundaries, integrates the three streams, resolves script load order, and reviews the final changes against the assignment.
2. Run offline tests and existing lint/format checks. Use JavaScript syntax checks and focused behavioral checks for capture, selection, errors, and navigation as appropriate. Do not rerun paid planner evals solely for visual changes.
3. Verify saved playback and local backend mode, including full responses, copied/downloaded JSON, unsupported answers, errors, citation clicks, tab state, and narrow layouts. Browser use requires the user's permission under the project instructions; request it when ready for visual verification. Do not claim browser verification from source inspection alone.
4. Confirm the inspector data matches the captured service response, and that a renderer failure does not hide it. Confirm no second network request is made just to inspect an answer.
5. After implementation is committed, regenerate the submission ZIP and repeat the appropriate existing smoke checks so it includes new frontend assets. Report checks actually run and any remaining limitations. Publishing is a separate action from this implementation plan.

## Acceptance criteria

- The existing query endpoint and response contract continue to work.
- A reviewer can see the complete service JSON separately from the rendered chart.
- Supported questions and limitations are easy to discover.
- The UI looks more considered without adding animation or architectural complexity.
- Static playback and live operation remain clearly distinguishable and usable.
- Documentation matches implemented behavior; no pending work is presented as complete.
