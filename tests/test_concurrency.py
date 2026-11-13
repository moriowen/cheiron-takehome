"""Shared resources under concurrent requests (FastAPI runs sync endpoints in a thread pool)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from trialviz.ctgov.client import (
    BudgetExceeded,
    CTGovClient,
    FetchResult,
    RateLimiter,
    StudyQuery,
    budget,
)
from trialviz.retrieve import Retriever, TTLCache


def test_rate_limiter_is_one_budget_across_threads():
    limiter = RateLimiter(rate_per_s=50, burst=1)
    started = time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: limiter.acquire(), range(11)))
    assert time.monotonic() - started >= 10 / 50 * 0.9  # 1 at once, then 10 at 50/s


def test_cache_evicts_least_recently_used_and_expires():
    cache = TTLCache(ttl_s=3600, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # a is now the most recent
    cache.put("c", 3)
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (1, None, 3)
    expired = TTLCache(ttl_s=0)
    expired.put("x", 1)
    assert expired.get("x") is None and len(expired) == 0


class SlowClient:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def count(self, query):
        with self.lock:
            self.calls += 1
        time.sleep(0.1)
        return 5

    def fetch_studies(self, query, *, fields, limit=None):
        with self.lock:
            self.calls += 1
        time.sleep(0.1)
        return FetchResult([], 5, False)


def test_identical_concurrent_requests_send_one_registry_call():
    client = SlowClient()
    r = Retriever(client)
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _: r.retrieve(StudyQuery(condition="x"), ("f",)), range(6)))
    assert client.calls == 2  # one count + one fetch, not 12


def test_budget_stops_registry_calls():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"totalCount": 1})),
        base_url="https://ct.test",
    )
    client = CTGovClient(http=http)
    with budget(10):
        assert client.count(StudyQuery()) == 1
    with budget(1e-9), pytest.raises(BudgetExceeded):
        time.sleep(0.001)
        client.count(StudyQuery())
    assert client.count(StudyQuery()) == 1  # no budget outside the block
