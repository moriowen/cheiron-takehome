"""Real ClinicalTrials.gov calls. Run with `uv run pytest -m live`. Free, about 40 s (1 req/s)."""

import pytest

from trialviz.ctgov.client import CTGovClient, CTGovError, StudyQuery
from trialviz.plan import Dimension
from trialviz.registry import DIMENSIONS

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    return CTGovClient(rate_per_s=1.0)


def test_version(client):
    assert client.version().api_version.startswith("2.")


def test_drug_synonyms_give_same_count(client):
    counts = {client.count(StudyQuery(intervention=n)) for n in ["pembrolizumab", "Keytruda"]}
    assert len(counts) == 1


def test_fetch_pages_and_limit(client):
    q = StudyQuery(intervention="pembrolizumab")
    r = client.fetch_studies(q, fields=["NCTId", "Phase"], page_size=100, limit=250)
    assert len(r.studies) == 250
    assert r.total_count > 250 and r.truncated
    ids = [s["protocolSection"]["identificationModule"]["nctId"] for s in r.studies]
    assert len(set(ids)) == 250  # paging returns no duplicates


def test_advanced_filter_narrows(client):
    q = StudyQuery(intervention="pembrolizumab")
    assert client.count(q.and_advanced("AREA[Phase]PHASE3")) < client.count(q)


def test_bad_field_is_a_clear_error(client):
    with pytest.raises(CTGovError, match="invalid field name"):
        client.fetch_studies(StudyQuery(), fields=["Bogus"], limit=1)


@pytest.mark.parametrize("dim", [Dimension.PHASE, Dimension.STATUS, Dimension.SPONSOR_CLASS])
def test_bucket_clauses_partition_the_registry(client, dim):
    """D32: the bucket counts of a single-valued dimension sum to the query's totalCount."""
    q = StudyQuery(condition="asthma", advanced="AREA[StartDate]RANGE[2015-01-01, MAX]")
    parts = [client.count(q.and_advanced(clause)) for _, clause in DIMENSIONS[dim].buckets]
    assert sum(parts) == client.count(q)
