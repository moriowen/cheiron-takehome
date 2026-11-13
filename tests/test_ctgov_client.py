import httpx
import pytest

from trialviz.ctgov.client import CTGovClient, CTGovError, StudyQuery


def make_client(handler, **kw) -> CTGovClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://ct.test/api/v2")
    return CTGovClient(**{"http": http, "backoff_s": 0, **kw})


def study(nct: str) -> dict:
    return {"protocolSection": {"identificationModule": {"nctId": nct}}}


# --- StudyQuery -> API params ---------------------------------------------------------------


def test_query_maps_to_api_params():
    q = StudyQuery(
        condition="lung cancer",
        intervention="pembrolizumab",
        sponsor="Merck",
        location="Germany",
        overall_status=["RECRUITING", "COMPLETED"],
        advanced="AREA[Phase]PHASE3",
    )
    assert q.to_params() == {
        "query.cond": "lung cancer",
        "query.intr": "pembrolizumab",
        "query.spons": "Merck",
        "query.locn": "Germany",
        "filter.overallStatus": "RECRUITING,COMPLETED",
        "filter.advanced": "AREA[Phase]PHASE3",
    }


def test_empty_query_has_no_params():
    assert StudyQuery().to_params() == {}


def test_and_advanced_combines_clauses():
    q = StudyQuery(advanced="AREA[Phase]PHASE3").and_advanced("AREA[StartDate]RANGE[2020,MAX]")
    assert q.advanced == "(AREA[Phase]PHASE3) AND (AREA[StartDate]RANGE[2020,MAX])"
    assert StudyQuery().and_advanced("X").advanced == "X"


# --- count ----------------------------------------------------------------------------------


def test_count_asks_for_one_slim_record():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(req.url.params)
        return httpx.Response(200, json={"totalCount": 2210, "studies": [study("NCT1")]})

    assert make_client(handler).count(StudyQuery(intervention="aspirin")) == 2210
    assert seen["countTotal"] == "true"
    assert seen["pageSize"] == "1"
    assert seen["fields"] == "NCTId"
    assert seen["query.intr"] == "aspirin"


# --- fetch_studies (pagination) -------------------------------------------------------------


def paged_handler(pages: list[list[str]], total: int, calls: list):
    """Serves `pages` in order; totalCount only on the first page, like the real API."""

    def handler(req: httpx.Request):
        calls.append(dict(req.url.params))
        i = int(req.url.params.get("pageToken", "0"))
        body = {"studies": [study(n) for n in pages[i]]}
        if i == 0 and req.url.params.get("countTotal") == "true":
            body["totalCount"] = total
        if i + 1 < len(pages):
            body["nextPageToken"] = str(i + 1)
        return httpx.Response(200, json=body)

    return handler


def test_fetch_follows_page_tokens_until_the_end():
    calls = []
    client = make_client(paged_handler([["A", "B"], ["C", "D"], ["E"]], total=5, calls=calls))
    result = client.fetch_studies(StudyQuery(condition="x"), fields=["NCTId", "Phase"], page_size=2)

    assert [s["protocolSection"]["identificationModule"]["nctId"] for s in result.studies] == list(
        "ABCDE"
    )
    assert result.total_count == 5
    assert not result.truncated
    assert [c.get("pageToken") for c in calls] == [None, "1", "2"]
    assert all(c["fields"] == "NCTId,Phase" and c["pageSize"] == "2" for c in calls)


def test_fetch_stops_at_limit_and_reports_truncation():
    calls = []
    client = make_client(paged_handler([["A", "B"], ["C", "D"], ["E"]], total=5, calls=calls))
    result = client.fetch_studies(StudyQuery(), fields=["NCTId"], page_size=2, limit=3)

    assert len(result.studies) == 3
    assert result.total_count == 5
    assert result.truncated
    assert len(calls) == 2  # did not fetch page 3


def test_page_size_never_exceeds_limit():
    calls = []
    client = make_client(paged_handler([["A"]], total=1, calls=calls))
    client.fetch_studies(StudyQuery(), fields=["NCTId"], page_size=1000, limit=50)
    assert calls[0]["pageSize"] == "50"


def test_page_size_is_capped_at_api_max():
    with pytest.raises(ValueError, match="1000"):
        make_client(lambda r: httpx.Response(200)).fetch_studies(
            StudyQuery(), fields=["NCTId"], page_size=1001
        )


# --- get_study / version --------------------------------------------------------------------


def test_get_study_and_version():
    def handler(req: httpx.Request):
        if req.url.path.endswith("/version"):
            return httpx.Response(
                200, json={"apiVersion": "2.0.5", "dataTimestamp": "2026-11-12T10:00:05"}
            )
        return httpx.Response(200, json=study("NCT02142738"))

    client = make_client(handler)
    assert client.get_study("NCT02142738")["protocolSection"]["identificationModule"]["nctId"]
    v = client.version()
    assert (v.api_version, v.data_timestamp) == ("2.0.5", "2026-11-12T10:00:05")


def test_get_study_rejects_malformed_id_before_calling():
    client = make_client(lambda r: pytest.fail("should not call the API"))
    with pytest.raises(ValueError, match="NCT"):
        client.get_study("XYZ")


# --- errors and retries ---------------------------------------------------------------------


def test_client_error_raises_with_api_message_and_no_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, text="Parameter 'fields' contains invalid field name: 'Bogus'")

    with pytest.raises(CTGovError, match="invalid field name") as exc:
        make_client(handler).count(StudyQuery())
    assert exc.value.status_code == 400
    assert len(calls) == 1


def test_not_found_is_a_ctgov_error():
    handler = lambda r: httpx.Response(404, text="NCT number NCT09999999 not found")  # noqa: E731
    with pytest.raises(CTGovError) as exc:
        make_client(handler).get_study("NCT09999999")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("fail", [503, 429, "timeout"])
def test_transient_failures_are_retried(fail):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            if fail == "timeout":
                raise httpx.ReadTimeout("slow", request=req)
            return httpx.Response(fail)
        return httpx.Response(200, json={"totalCount": 7, "studies": []})

    assert make_client(handler, max_retries=3).count(StudyQuery()) == 7
    assert len(calls) == 3


def test_gives_up_after_max_retries():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="down")

    with pytest.raises(CTGovError) as exc:
        make_client(handler, max_retries=2).count(StudyQuery())
    assert exc.value.status_code == 503
    assert len(calls) == 3  # first try + 2 retries


def test_retry_after_header_sets_the_delay(monkeypatch):
    slept = []
    monkeypatch.setattr("trialviz.ctgov.client.time.sleep", slept.append)
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"totalCount": 1, "studies": []}),
        ]
    )
    make_client(lambda r: next(replies)).count(StudyQuery())
    assert slept == [7.0]


def test_rate_limiter_allows_a_burst_then_the_sustained_rate(monkeypatch):
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    monkeypatch.setattr("trialviz.ctgov.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("trialviz.ctgov.client.time.sleep", sleep)
    client = make_client(
        lambda r: httpx.Response(200, json={"totalCount": 1, "studies": []}),
        rate_per_s=2.0,
        burst=3,
    )
    for _ in range(5):
        client.count(StudyQuery())
    assert slept == [pytest.approx(0.5), pytest.approx(0.5)]  # 3 at once, then 2 per second
    now[0] += 10  # idle refills the bucket, capped at the burst size
    for _ in range(3):
        client.count(StudyQuery())
    assert len(slept) == 2


def test_suggest_calls_the_site_autocomplete_and_unescapes():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        return httpx.Response(200, json=["pembrolizumab", "Pembrolizumab Injection \\[Keytruda\\]"])

    out = make_client(handler).suggest("pembrolizimab", "InterventionName")
    assert seen["url"].startswith("https://ct.test/api/int/suggest?")
    assert "dictionary=InterventionName" in seen["url"]
    assert out == ["pembrolizumab", "Pembrolizumab Injection [Keytruda]"]
