"""The versioned golden dataset: hand-written cases plus reviewed synthetic cases.

Every eval result records DATASET_VERSION and PROMPT_VERSION, so any score can be reproduced
from git history. Bump DATASET_VERSION whenever a case is added, removed, or changed.
"""

import json
from pathlib import Path

from evals.planner_cases import CASES

DATASET_VERSION = "2026-11-12.4"
DATA_DIR = Path(__file__).parent / "data"
SYNTHETIC_FILE = DATA_DIR / "synthetic.json"

INTENTS = {
    "trend",
    "distribution",
    "top_n",
    "geographic",
    "comparison",
    "crosstab",
    "network",
    "histogram",
    "scatter",
    "total",
    "out_of_scope",
}
DIFFICULTIES = {"easy", "medium", "hard"}
SOURCES = {"appendix", "taxonomy", "edge_case", "regression", "synthetic"}


def load_cases(*, synthetic: bool = True) -> list[dict]:
    cases = list(CASES)
    if synthetic and SYNTHETIC_FILE.exists():
        cases += json.loads(SYNTHETIC_FILE.read_text())["cases"]
    return cases
