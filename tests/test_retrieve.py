"""Retriever: which counting method each query gets, by the number of API requests (D32, D33)."""

from trialviz.ctgov.client import FetchResult, StudyQuery
from trialviz.retrieve import Retriever


class FakeClient:
    """Answers counts with `total`, bucket queries with 7 matches, fetches with `limit` rows."""

    def __init__(self, total: int):
        self.total = total
        self.calls: list[tuple] = []

    def count(self, query):
        self.calls.append(("count", query.advanced))
        return self.total

    def fetch_studies(self, query, *, fields, limit=None):
        self.calls.append(("fetch", query.advanced, limit))
        n = self.total if query.advanced is None else 7
        rows = [
            {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}
            for i in range(min(n, limit or n))
        ]
        return FetchResult(rows, n, truncated=len(rows) < n)


BUCKETS = tuple((f"K{i}", f"AREA[X]K{i}") for i in range(9))


def run(total, buckets=(), **kw):
    client = FakeClient(total)
    out = Retriever(client, **{"max_records": 20_000, "sample_records": 3000, **kw}).retrieve(
        StudyQuery(condition="x"), ("f",), buckets
    )
    return out, client.calls


def test_small_query_fetches_every_record_even_with_buckets():
    out, calls = run(3006, BUCKETS)  # 4 pages beat 9 bucket counts
    assert out.method == "all_records" and out.trials_counted == 3006 and out.exact
    assert calls == [("count", None), ("fetch", None, None)]


def test_bucket_counts_when_they_need_fewer_requests():
    out, calls = run(14_719, BUCKETS)  # 15 pages vs 9 counts
    assert out.method == "registry_count"
    assert out.bucket_counts == {k: 7 for k, _ in BUCKETS}
    assert out.trials_counted == 14_719 and out.exact
    assert all(c[2] == 5 for c in calls if c[0] == "fetch")


def test_over_the_cap_bucket_counts_stay_exact():
    out, _ = run(124_749, BUCKETS)
    assert out.method == "registry_count" and out.total_count == 124_749


def test_over_the_cap_without_buckets_is_a_relevance_sample():
    out, calls = run(124_749)
    assert out.method == "sample" and not out.exact
    assert out.trials_counted == 3000 and calls[-1] == ("fetch", None, 3000)


def test_up_to_the_cap_without_buckets_is_exact():
    out, _ = run(19_000)
    assert out.method == "all_records" and out.trials_counted == 19_000


def test_bucket_samples_are_deduplicated_by_trial():
    out, _ = run(14_719, BUCKETS)  # every bucket returns the same 5 NCT IDs
    assert len(out.studies) == 5


def test_counts_and_fetches_are_cached():
    client = FakeClient(14_719)
    r = Retriever(client)
    r.retrieve(StudyQuery(condition="x"), ("f",), BUCKETS)
    n = len(client.calls)
    r.retrieve(StudyQuery(condition="x"), ("f",), BUCKETS)
    assert len(client.calls) == n
