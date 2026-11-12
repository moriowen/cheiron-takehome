"""Real ClinicalTrials.gov calls. Run with `uv run pytest -m live`. Free, about 5 seconds."""

import pytest

from trialviz.ctgov.client import CTGovClient, CTGovError, StudyQuery

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    return CTGovClient()


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
