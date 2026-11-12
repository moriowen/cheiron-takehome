"""Records real API responses as test fixtures (D16). Re-run only when the registry fields change.

Run: uv run python scripts/record_fixtures.py
"""

import json
from pathlib import Path

from trialviz.ctgov.client import CTGovClient, StudyQuery
from trialviz.registry import ALL_FIELDS

OUT = Path(__file__).parents[1] / "tests" / "fixtures"
SETS = {
    "pembrolizumab": StudyQuery(intervention="pembrolizumab"),
    "melanoma": StudyQuery(condition="melanoma"),
}
LIMIT = 200

client = CTGovClient(min_interval_s=0.3)
version = client.version()
for name, query in SETS.items():
    r = client.fetch_studies(query, fields=ALL_FIELDS, limit=LIMIT)
    body = {
        "query": query.to_params(),
        "data_timestamp": version.data_timestamp,
        "total_count": r.total_count,
        "studies": r.studies,
    }
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(body, indent=1))
    print(f"{path.name}: {len(r.studies)} of {r.total_count}, {path.stat().st_size // 1024} KB")

# Single records with known edge cases.
for nct in ["NCT02142738"]:  # locations removed; multi-drug arms
    path = OUT / f"{nct}.json"
    path.write_text(json.dumps(client.get_study(nct, fields=ALL_FIELDS), indent=1))
    print(f"{path.name}")
