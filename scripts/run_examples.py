"""Runs the example queries through the real pipeline and saves request + response JSON.

Run: uv run python scripts/run_examples.py
Output: examples/NN-<slug>.json (actual system output, not hand-written).
"""

import json
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
]


def main() -> None:
    app = AppSettings()
    pipeline = Pipeline(
        OpenAIPlanner(LLMSettings()),
        CTGovClient(min_interval_s=app.ctgov_min_interval_s),
        max_records=app.max_records,
    )
    OUT.mkdir(exist_ok=True)
    for i, (slug, body) in enumerate(EXAMPLES, 1):
        resp = pipeline.run(QueryRequest(**body))
        path = OUT / f"{i:02d}-{slug}.json"
        path.write_text(
            json.dumps({"request": body, "response": resp.model_dump(mode="json")}, indent=2)
        )
        kind = resp.visualization.type.value if resp.visualization else resp.status
        print(f"{path.name}: {kind}, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
