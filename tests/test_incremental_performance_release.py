from __future__ import annotations

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
from bankrotai.services.ingestion import incremental_source_specs, source_retry_specs
from bankrotai.tasks import _scheduled_source_preflight, celery_app


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
    specs = {spec.source_id: spec for spec in incremental_source_specs()}
    assert specs["m-ets.ru"].reuse_unchanged_details is True
    assert specs["cdtrf.ru"].reuse_unchanged_details is True
    assert specs["nistp.ru"].reuse_unchanged_details is False


def test_targeted_retry_requests_resume_for_production_spec() -> None:
    mets = source_retry_specs("m-ets.ru")[0]
    cdtrf = source_retry_specs("cdtrf.ru")[0]
    assert mets.resume_from_checkpoint and mets.reuse_unchanged_details
    assert cdtrf.resume_from_checkpoint and cdtrf.reuse_unchanged_details


def test_preflight_fails_before_heavy_run(monkeypatch) -> None:
    from bankrotai.services import source_resilience

    def probe(source_id: str, *, timeout_seconds: float = 5.0):
        return {"success": source_id != "bad", "source_system": source_id}

    monkeypatch.setattr(source_resilience, "probe_source_endpoint", probe)
    result = _scheduled_source_preflight((
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
