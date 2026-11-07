"""Offline detail concurrency regression tests (no provider or database I/O)."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from bankrotai.connectors.registry.detail_transport import DetailWorkers, HostRateLimiter
from bankrotai.connectors.registry import cdtrf_sync, mets_sync


def test_per_host_rate_limiter_and_concurrency():
    limiter = HostRateLimiter(20 / 10)  # two starts per second
    stamps = []
    gate = threading.Lock()
    from concurrent.futures import ThreadPoolExecutor

    def request(_):
        limiter.acquire("https://torgi.cdtrf.ru/trades/123")
        with gate:
            stamps.append(time.monotonic())

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(request, range(3)))
    assert len(stamps) == 3
    assert min(b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:])) >= 0.47


@pytest.mark.parametrize("bad", [0, -1, 11])
def test_rate_limit_invalid(bad):
    with pytest.raises(ValueError):
        HostRateLimiter(bad)



@pytest.mark.parametrize("filters_cls", [cdtrf_sync.CdtrfSyncFilters, mets_sync.MetsSyncFilters])
def test_source_detail_rate_cap_is_conservative(filters_cls):
    with pytest.raises(ValueError, match="detail_requests_per_second"):
        filters_cls(detail_requests_per_second=2.01)


@pytest.mark.parametrize("transient_status", [429, 503])
def test_mets_retry_attempts_are_all_rate_limited(monkeypatch, transient_status):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.content = b"detail"
            self.text = "detail"
            self.headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    class Session:
        def __init__(self):
            self.responses = [Response(transient_status), Response(200)]

        def get(self, *args, **kwargs):
            return self.responses.pop(0)

    starts = []
    pilot = mets_sync.MetsPublicPilot(session=Session())
    monkeypatch.setattr("bankrotai.connectors.registry.mets_public.time.sleep", lambda _: None)
    result = pilot._get(
        "https://m-ets.ru/1234-1",
        before_request=lambda url: starts.append(url),
    )
    assert result == "detail"
    assert starts == ["https://m-ets.ru/1234-1", "https://m-ets.ru/1234-1"]



def test_mets_timeout_retries_are_bounded_and_rate_gated(monkeypatch):
    class Session:
        def get(self, *args, **kwargs):
            raise requests.Timeout("offline fixture timeout")

    starts = []
    pilot = mets_sync.MetsPublicPilot(session=Session())
    monkeypatch.setattr("bankrotai.connectors.registry.mets_public.time.sleep", lambda _: None)

    with pytest.raises(requests.Timeout):
        pilot._get(
            "https://m-ets.ru/1234-1",
            before_request=lambda url: starts.append(url),
        )

    assert len(starts) == 5


def test_async_search_keeps_event_loop_responsive(monkeypatch):
    monkeypatch.setattr(
        mets_sync, "_request_page",
        lambda *args, **kwargs: SimpleNamespace(text="catalogue"),
    )
    monkeypatch.setattr(mets_sync, "_parse_page", lambda *args, **kwargs: {
        "total": 1,
        "next_page": None,
        "observations": [
            ("1234-1", "100", "Торги в стадии приема заявок", "flat"),
        ],
    })
    monkeypatch.setattr(mets_sync, "_property_ready", lambda lot: True)
    monkeypatch.setattr(
        mets_sync,
        "parse_mets_lot",
        lambda html, url: SimpleNamespace(
            external_id="1234-1",
            auction_status="active",
            start_price=100,
            current_price=None,
            raw_data={},
        ),
    )

    pilot = mets_sync.MetsPublicPilot(session=object())
    monkeypatch.setattr(
        pilot, "_get",
        lambda url: (time.sleep(0.15), url)[1],
    )
    crawler = mets_sync.MetsSourceCrawler(pilot)

    async def scenario():
        task = asyncio.create_task(crawler.search(
            mets_sync.MetsSyncFilters(
                max_pages=1,
                detail_concurrency=2,
                detail_requests_per_second=2,
            )
        ))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        result = await task
        assert len(result.items) == 1

    asyncio.run(scenario())


def test_worker_pool_concurrency_cap():
    workers = DetailWorkers(requests.Session(), 2, 10)
    active = peak = 0
    lock = threading.Lock()

    def detail(_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        with lock:
            active -= 1

    with workers.pool() as pool:
        list(pool.map(detail, range(8)))
    assert peak == 2



def test_fail_fast_map_never_queues_beyond_in_flight_limit():
    workers = DetailWorkers(requests.Session(), 2, 10)
    started = []
    lock = threading.Lock()

    def detail(index):
        with lock:
            started.append(index)
        if index == 0:
            raise RuntimeError("fatal detail")
        time.sleep(0.1)
        return index

    with workers.pool() as pool:
        with pytest.raises(RuntimeError, match="fatal detail"):
            list(workers.map_ordered(pool, detail, range(20)))
    assert set(started).issubset({0, 1})


def test_shared_limiter_can_span_worker_batches(monkeypatch):
    from bankrotai.connectors.registry import detail_transport

    now = [100.0]
    sleeps = []

    monkeypatch.setattr(detail_transport.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(detail_transport.time, "sleep", sleep)
    limiter = HostRateLimiter(2)
    first = DetailWorkers(requests.Session(), 2, 2, limiter=limiter)
    second = DetailWorkers(requests.Session(), 2, 2, limiter=limiter)

    first.limiter.acquire("https://m-ets.ru/1234-1")
    second.limiter.acquire("https://m-ets.ru/1234-2")

    assert sleeps == [pytest.approx(0.5)]


@pytest.mark.parametrize("error", [503, 429])
def test_cdtrf_http_retry_policy(monkeypatch, error):
    calls = []
    rate_starts = []
    def fetch(*args, **kwargs):
        calls.append(1)
        if error == 503 and len(calls) == 1:
            raise cdtrf_sync.CdtrfCanaryError("CDTRF HTTP 503")
        if error == 429:
            raise cdtrf_sync.CdtrfCanaryError("CDTRF HTTP 429")
        return SimpleNamespace(external_id="cdtrf:1")
    monkeypatch.setattr(cdtrf_sync, "fetch_verified_cdtrf_lot", fetch)
    monkeypatch.setattr(cdtrf_sync.time, "sleep", lambda _: None)
    limiter = SimpleNamespace(
        acquire=lambda url: rate_starts.append(url),
    )
    if error == 429:
        with pytest.raises(cdtrf_sync.CdtrfCanaryError, match="429"):
            cdtrf_sync._verified_detail_with_retry(
                "1", "1", requests.Session(), limiter,
            )
        assert len(calls) == 1
        assert len(rate_starts) == 1
    else:
        cdtrf_sync._verified_detail_with_retry(
            "1", "1", requests.Session(), limiter,
        )
        assert len(calls) == 2
        assert len(rate_starts) == 2


def test_cdtrf_timeout_fails_after_bounded_retries(monkeypatch):
    attempts = []
    def timeout(*args, **kwargs):
        attempts.append(1)
        raise requests.Timeout("offline fixture timeout")
    monkeypatch.setattr(cdtrf_sync, "fetch_verified_cdtrf_lot", timeout)
    monkeypatch.setattr(cdtrf_sync.time, "sleep", lambda _: None)
    with pytest.raises(requests.Timeout):
        cdtrf_sync._verified_detail_with_retry("1", "1", requests.Session())
    assert len(attempts) == 3


def test_cdtrf_multi_lot_page_parallel_preserves_order(monkeypatch):
    session = requests.Session()
    session.get = lambda *args, **kwargs: SimpleNamespace(  # type: ignore[method-assign]
        status_code=200, content=b"{}", headers={"Content-Type": "application/json"},
        json=lambda: {},
    )
    hits = [{"trade_id": str(i), "trade_lot_id": str(i),
             "external_id": f"cdtrf:{i}"} for i in range(1, 5)]
    monkeypatch.setattr(cdtrf_sync, "parse_cdtrf_api_page", lambda *a, **kw: {
        "reported_total": 4, "observed_rows": 4,
        "observed_lot_ids": [str(i) for i in range(1, 5)],
        "property_candidates": hits,
    })
    active = peak = 0
    lock = threading.Lock()
    def fetch(trade, lot, session, limiter=None):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        time.sleep(0.04)
        with lock:
            active -= 1
        return SimpleNamespace(
            external_id=f"cdtrf:{lot}", auction_status="active",
            raw_data={}, cadastral_number="77:01:0000000:1", lot_url="https://example.test",
            start_price=100,
        )
    monkeypatch.setattr(cdtrf_sync, "_verified_detail_with_retry", fetch)
    monkeypatch.setattr(cdtrf_sync, "_property_ready", lambda lot: True)
    result = asyncio.run(cdtrf_sync.CdtrfSourceCrawler(session).search(
        cdtrf_sync.CdtrfSyncFilters(max_pages=1, detail_concurrency=2,
                                    detail_requests_per_second=2)))
    assert [lot.external_id for lot in result.items] == [f"cdtrf:{i}" for i in range(1, 5)]
    assert peak == 2
    assert result.next_cursor is None
    assert result.metadata["reconcile_missing"] is False
    assert result.metadata["safe_for_archival"] is False


def test_mets_multi_lot_page_and_price_guard(monkeypatch):
    class Session:
        def get(self, *a, **kw):
            return SimpleNamespace(status_code=200, content=b"detail",
                                   text="detail", headers={"Content-Type": "text/html"})
    session = Session()
    pilot = SimpleNamespace(session=session)
    ids = ["1234-1", "1234-2"]
    monkeypatch.setattr(mets_sync, "_request_page", lambda *a, **kw: SimpleNamespace(text="catalogue"))
    monkeypatch.setattr(mets_sync, "_parse_page", lambda *a, **kw: {
        "total": 2, "next_page": None,
        "observations": [(i, "100", "Торги в стадии приема заявок", "flat") for i in ids],
    })
    monkeypatch.setattr(mets_sync.MetsPublicPilot, "_get", lambda *a: "detail")
    monkeypatch.setattr(mets_sync, "_property_ready", lambda lot: True)
    def parse(_html, url):
        return SimpleNamespace(
            external_id=url.rsplit("/", 1)[-1], auction_status="active",
            start_price=100, current_price=None, raw_data={},
        )
    monkeypatch.setattr(mets_sync, "parse_mets_lot", parse)
    result = asyncio.run(mets_sync.MetsSourceCrawler(pilot).search(
        mets_sync.MetsSyncFilters(max_pages=1, detail_concurrency=2,
                                  detail_requests_per_second=2)))
    assert [lot.external_id for lot in result.items] == ids
    assert result.metadata["detail_attempts"] == 2
    assert result.metadata["reconcile_missing"] is False
    assert result.metadata["safe_for_archival"] is False
