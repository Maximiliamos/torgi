from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import requests

from bankrotai.connectors.registry.cdtrf_sync import CdtrfSourceCrawler
from bankrotai.connectors.registry.mets_public import APARTMENTS_URL, MetsPublicPilot
from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler
from bankrotai.services.incremental_detail_cache import (
    catalogue_fingerprint,
    snapshot_reusable,
)
from bankrotai.services.ingestion import default_source_specs
from bankrotai.services.ingestion_incremental import incremental_specs, retry_specs
from bankrotai.services.scheduled_source_preflight import run_scheduled_source_preflight
from bankrotai.tasks import celery_app


def _snapshot(*, title: str = "Объект", status: str = "active", price: str = "100") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "external_id": "x",
        "title": title,
        "start_price": price,
        "current_price": None,
        "source_status": status,
        "cadastral_number": "76:23:010101:1",
        "lot_url": "https://example.invalid/lot",
        "is_archived": False,
        "detail_verified_at": now,
        "bootstrap_verified_at": now,
        "catalogue_fingerprint": None,
    }


def test_detail_cache_reuses_only_fresh_matching_business_evidence() -> None:
    current = datetime.now(timezone.utc)
    snap = _snapshot()
    assert snapshot_reusable(
        snap,
        expected_price=Decimal("100"),
        expected_status="active",
        expected_title="Объект",
        now=current,
    )
    assert not snapshot_reusable(
        snap,
        expected_price=Decimal("101"),
        expected_status="active",
        expected_title="Объект",
        now=current,
    )
    stale = dict(snap)
    stale["detail_verified_at"] = (current - timedelta(days=8)).isoformat()
    stale["bootstrap_verified_at"] = stale["detail_verified_at"]
    assert not snapshot_reusable(
        stale,
        expected_price=Decimal("100"),
        expected_status="active",
        expected_title="Объект",
        now=current,
    )


def test_mets_changed_detail_only_and_checkpoint_round_trip() -> None:
    pilot = MetsPublicPilot(session=requests.Session())
    crawler = MetsSourceCrawler(pilot)
    item = ("242937-2", Decimal("100"), "Торги в стадии приема заявок", "Объект")
    snap = _snapshot()
    snap["external_id"] = "242937-2"
    crawler.set_detail_cache({"242937-2": snap})
    assert crawler._can_reuse_detail(item) is True

    crawler._seen = {"242937-2", "242938-1"}
    crawler._total = 40
    crawler._minimum_total = 39
    crawler._maximum_total = 40
    crawler._page_size = 20
    crawler._overlap_count = 1
    crawler._expected_page = 2
    crawler._prior_digest = "a" * 64
    checkpoint = crawler._resume_checkpoint("2:" + "a" * 64)
    assert checkpoint is not None

    restored = MetsSourceCrawler(MetsPublicPilot(session=requests.Session()))
    assert restored.restore_checkpoint(checkpoint) == "2:" + "a" * 64
    assert restored._seen == crawler._seen
    assert restored._expected_page == 2


def test_cdtrf_changed_detail_only_and_checkpoint_round_trip() -> None:
    crawler = CdtrfSourceCrawler(requests.Session())
    hit = {
        "external_id": "cdtrf:123",
        "trade_id": "456",
        "trade_lot_id": "123",
        "listing_price_rub": 100.0,
        "source_status_text": "Прием заявок",
        "region_id": 76,
        "property_category_ids": [41],
    }
    snap = _snapshot(title="Любой заголовок")
    snap["external_id"] = "cdtrf:123"
    crawler.set_detail_cache({"cdtrf:123": snap})
    assert crawler._can_reuse_detail(hit) is True

    crawler._seen = {"123", "124"}
    crawler._total = 200
    crawler._pages_total = 2
    crawler._total_changes = 1
    crawler._expected_page = 2
    crawler._prior_digest = "b" * 64
    checkpoint = crawler._resume_checkpoint("2:" + "b" * 64)
    assert checkpoint is not None

    restored = CdtrfSourceCrawler(requests.Session())
    assert restored.restore_checkpoint(checkpoint) == "2:" + "b" * 64
    assert restored._seen == crawler._seen
    assert restored._expected_page == 2


def test_twice_daily_schedule_uses_incremental_detail_mode() -> None:
    beat = celery_app.conf.beat_schedule["refresh-nationwide-sources-full"]
    assert beat["args"] == ("incremental",)
    specs = {spec.source_id: spec for spec in incremental_specs(default_source_specs())}
    assert specs["m-ets.ru"].reuse_unchanged_details is True
    assert specs["cdtrf.ru"].reuse_unchanged_details is True
    assert specs["nistp.ru"].reuse_unchanged_details is False


def test_targeted_retry_requests_resume_for_production_spec() -> None:
    mets = retry_specs(default_source_specs(), "m-ets.ru")[0]
    cdtrf = retry_specs(default_source_specs(), "cdtrf.ru")[0]
    assert mets.resume_from_checkpoint and mets.reuse_unchanged_details
    assert cdtrf.resume_from_checkpoint and cdtrf.reuse_unchanged_details


def test_preflight_fails_before_heavy_run(monkeypatch) -> None:
    from bankrotai.services import source_resilience

    def probe(source_id: str, *, timeout_seconds: float = 5.0):
        return {"success": source_id != "bad", "source_system": source_id}

    monkeypatch.setattr(source_resilience, "probe_source_endpoint", probe)
    result = run_scheduled_source_preflight((
        SimpleNamespace(source_id="good"),
        SimpleNamespace(source_id="bad"),
    ))
    assert result["success"] is False
    assert result["failed_sources"] == ["bad"]


def test_mets_scope_excludes_standalone_land_category() -> None:
    assert APARTMENTS_URL.endswith(
        "eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiwzNCwzOSwzNywzOCwyOCw0MSJ9"
    )


def test_catalogue_fingerprint_is_order_stable() -> None:
    assert catalogue_fingerprint({"a": 1, "b": 2}) == catalogue_fingerprint({"b": 2, "a": 1})



def test_mets_catalogue_first_reuses_unchanged_without_detail(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as module
    from types import SimpleNamespace

    pages = {
        1: {
            "total": 3,
            "observations": [
                ("1001-1", "100", "Торги в стадии приема заявок", "Объект 1"),
                ("1002-1", "200", "Объявленные торги", "Объект 2"),
            ],
            "next_page": 2,
        },
        2: {
            "total": 3,
            "observations": [
                ("1003-1", "300", "Объявленные торги", "Объект 3"),
            ],
            "next_page": None,
        },
    }
    monkeypatch.setattr(
        module, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text=str(page)),
    )
    monkeypatch.setattr(module, "_parse_page", lambda text, page: pages[page])
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(
        pilot, "_get",
        lambda _url: (_ for _ in ()).throw(AssertionError("unchanged detail fetched")),
    )
    crawler = MetsSourceCrawler(pilot)
    filters = MetsSyncFilters()
    discovery = crawler.prepare_catalogue(filters)
    assert discovery["pages"] == 2

    now = datetime.now(timezone.utc).isoformat()
    snapshots = {}
    for external_id, price, status, title in pages[1]["observations"] + pages[2]["observations"]:
        snapshots[external_id] = {
            "external_id": external_id,
            "title": title,
            "start_price": price,
            "current_price": None,
            "source_status": "active" if "приема заявок" in status else "scheduled",
            "cadastral_number": "76:23:010101:1",
            "lot_url": f"https://m-ets.ru/{external_id}",
            "is_archived": False,
            "detail_verified_at": now,
            "bootstrap_verified_at": now,
            "catalogue_fingerprint": None,
        }
    crawler.set_detail_cache(snapshots)

    first = asyncio.run(crawler.search(filters))
    second = asyncio.run(crawler.search(filters, first.next_cursor))
    assert first.items == []
    assert second.items == []
    assert first.metadata["detail_attempts"] == 0
    assert first.metadata["detail_skipped_unchanged"] == 2
    assert second.metadata["detail_attempts"] == 0
    assert second.metadata["detail_skipped_unchanged"] == 1


def test_cdtrf_catalogue_first_reuses_unchanged_without_detail(monkeypatch) -> None:
    import bankrotai.connectors.registry.cdtrf_sync as module

    rows = [
        {
            "external_id": "cdtrf:101",
            "trade_id": "201",
            "trade_lot_id": "101",
            "listing_price_rub": 100,
            "source_status_text": "Прием заявок",
            "region_id": 76,
            "property_category_ids": [41],
        },
        {
            "external_id": "cdtrf:102",
            "trade_id": "202",
            "trade_lot_id": "102",
            "listing_price_rub": 200,
            "source_status_text": "Объявлены",
            "region_id": 76,
            "property_category_ids": [41],
        },
    ]
    data = {
        "reported_total": 2,
        "observed_rows": 2,
        "observed_lot_ids": ["101", "102"],
        "property_candidates": rows,
    }
    crawler = CdtrfSourceCrawler(requests.Session())
    monkeypatch.setattr(crawler, "_fetch_catalogue_page", lambda page, size: (data, 0))
    discovery = crawler.prepare_catalogue(module.CdtrfSyncFilters(page_size=100))
    assert discovery["pages"] == 1

    now = datetime.now(timezone.utc).isoformat()
    crawler.set_detail_cache({
        row["external_id"]: {
            "external_id": row["external_id"],
            "title": "Объект",
            "start_price": str(row["listing_price_rub"]),
            "current_price": None,
            "source_status": "active" if "Прием" in row["source_status_text"] else "scheduled",
            "cadastral_number": "76:23:010101:1",
            "lot_url": f"https://torgi.cdtrf.ru/trades/{row['trade_id']}",
            "is_archived": False,
            "detail_verified_at": now,
            "bootstrap_verified_at": now,
            "catalogue_fingerprint": None,
        }
        for row in rows
    })
    monkeypatch.setattr(
        module, "_verified_detail_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unchanged CDTRF detail fetched")
        ),
    )
    page = asyncio.run(
        crawler.search(module.CdtrfSyncFilters(page_size=100))
    )
    assert page.items == []
    assert page.metadata["detail_attempts"] == 0
    assert page.metadata["detail_skipped_unchanged"] == 2
    assert page.metadata["verified_property_lots"] == 2


def test_mets_resume_rejects_shifted_fresh_catalogue_prefix(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as module
    from types import SimpleNamespace

    pages = {
        1: {
            "total": 3,
            "observations": [
                ("1001-1", "100", "Объявленные торги", "Объект 1"),
                ("1002-1", "200", "Объявленные торги", "Объект 2"),
            ],
            "next_page": 2,
        },
        2: {
            "total": 3,
            "observations": [
                ("1003-1", "300", "Объявленные торги", "Объект 3"),
            ],
            "next_page": None,
        },
    }
    monkeypatch.setattr(
        module, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text=str(page)),
    )
    monkeypatch.setattr(module, "_parse_page", lambda text, page: pages[page])
    crawler = MetsSourceCrawler(MetsPublicPilot(session=object()))
    crawler.prepare_catalogue(MetsSyncFilters())
    old_digest = catalogue_fingerprint({"different": "old"})
    checkpoint = {
        "version": 1,
        "source": "m-ets.ru",
        "next_cursor": "2:" + old_digest,
        "seen_ids": ["old-id"],
        "total": 3,
        "minimum_total": 3,
        "maximum_total": 3,
        "page_size": 2,
        "overlap_count": 0,
    }
    assert crawler.restore_checkpoint(checkpoint) is None
