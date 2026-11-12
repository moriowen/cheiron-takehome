"""Sample use of CTGovClient: count pembrolizumab trials by phase.

Run: uv run python scripts/ctgov_sample.py
"""

from collections import Counter

from trialviz.ctgov.client import CTGovClient, StudyQuery

client = CTGovClient(min_interval_s=0.2)
query = StudyQuery(intervention="pembrolizumab")

print("data version:", client.version())
print("total matches:", client.count(query))

# Exact counts per phase: one cheap count call per bucket.
for phase in ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]:
    print(f"  {phase:13} {client.count(query.and_advanced(f'AREA[Phase]{phase}'))}")

# Or fetch slim records and count locally (needed for networks and citations).
result = client.fetch_studies(query, fields=["NCTId", "Phase"], limit=500)
phases = Counter(
    "|".join(s["protocolSection"].get("designModule", {}).get("phases", ["<none>"]))
    for s in result.studies
)
print(f"first {len(result.studies)} of {result.total_count} (truncated={result.truncated}):")
for value, n in phases.most_common():
    print(f"  {value:13} {n}")
