"""Runs the example queries through the real pipeline and saves request + response JSON.

Run: uv run python scripts/run_examples.py [slug ...]   (no slug: all examples)
Output: examples/NN-<slug>.json (actual system output, not hand-written).
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from trialviz.config import AppSettings, LLMSettings
from trialviz.ctgov.client import CTGovClient
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.pipeline import Pipeline
from trialviz.schemas import QueryRequest

OUT = Path(__file__).parents[1] / "examples"
EXAMPLES = [
    (
        "trend-with-hint",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
            "start_year": 2015,
        },
    ),
    (
        "compare-phases",
        {"query": "Compare phases for trials involving nivolumab vs pembrolizumab."},
    ),
    (
        "countries-recruiting",
        {"query": "Which countries have the most recruiting trials for multiple sclerosis?"},
    ),
    (
        "sponsor-drug-network",
        {"query": "Show a network of sponsors and drugs for melanoma trials."},
    ),
    (
        "drug-cooccurrence-network",
        {"query": "Which drugs frequently co-occur in combination studies for lung cancer?"},
    ),
    (
        "enrollment-histogram",
        {"query": "What is the distribution of enrollment sizes in phase 3 asthma trials?"},
    ),
    ("out-of-scope", {"query": "Is pembrolizumab more effective than nivolumab?"}),
    (
        "exact-phase-counts",  # 14,719 matches: exact registry counts per phase (D32)
        {"query": "How are lung cancer trials distributed across phases?"},
    ),
    (
        "misspelled-drug",  # "pembrolizimab" (3 trials) -> "pembrolizumab" (D34)
        {"query": "How are pembrolizimab trials distributed across phases?"},
    ),
    # Saved runs for the remaining guide buttons, so the static demo replays every one.
    (
        "trend-comparison",
        {"query": "Trials per year for semaglutide versus tirzepatide since 2018."},
    ),
    (
        "intervention-types",
        {"query": "What are the most common intervention types for diabetes trials?"},
    ),
    ("phase-by-status", {"query": "Break down COVID-19 trials by phase and status."}),
    ("top-sponsors", {"query": "Who are the top 10 sponsors of obesity trials?"}),
    ("recruiting-total", {"query": "How many recruiting trials are there for glioblastoma?"}),
    (
        "enrollment-vs-duration",
        {"query": "Plot enrollment against trial duration for semaglutide trials."},
    ),
]


def main() -> None:
    app = AppSettings()
    pipeline = Pipeline(
        OpenAIPlanner(LLMSettings()),
        CTGovClient(rate_per_s=app.ctgov_rate_per_s, burst=app.ctgov_burst),
        max_records=app.max_records,
        sample_records=app.sample_records,
        cache_ttl_s=app.cache_ttl_s,
        cache_max_entries=app.cache_max_entries,
        budget_s=app.request_budget_s,
    )
    OUT.mkdir(exist_ok=True)
    only = set(sys.argv[1:])
    for i, (slug, body) in enumerate(EXAMPLES, 1):
        if only and slug not in only:
            continue
        resp = pipeline.run(QueryRequest(**body))
        path = OUT / f"{i:02d}-{slug}.json"
        path.write_text(
            json.dumps(
                {
                    "request": body,
                    "response": resp.model_dump(mode="json"),
                    "generated_at": datetime.now(UTC).isoformat(),
                    "code_revision": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=OUT.parent, text=True
                    ).strip(),
                },
                indent=2,
            )
        )
        kind = resp.visualization.type.value if resp.visualization else resp.status
        print(f"{path.name}: {kind}, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
