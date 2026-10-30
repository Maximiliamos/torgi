"""No-production-write QA map tests, with synthetic connector evidence only."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bankrotai.services import etp_qa_map


def _lot(**kwargs):
    value = {
        "source_system": "etpu.ru",
        "external_id": "etpu.ru:auctions-123",
        "title": "Земельный участок",
        "lot_url": "https://bankrupt.etpu.ru/public/auctions/lots/view/123/",
        "cadastral_number": "50:05:0010203:1234",
        "start_price": 2000000.0,
        "current_price": None,
        "auction_status": "active",
        "detail_level": "list",
        "category": "land",
    }
    value.update(kwargs)
    return SimpleNamespace(**value)


def test_bounded_args_fail_without_any_external_calls(monkeypatch):
    async def reject(*args, **kwargs):
        pytest.fail("network must not be called for invalid bounds")
    monkeypatch.setattr(etp_qa_map, "_collect", reject)
    for source, pages, per_page, geo_limit in (
        ("unknown.ru", 1, 1, 0),
        ("etpu.ru", 3, 1, 0),
        ("etpu.ru", 1, 4, 0),
        ("etpu.ru", 1, 1, 4),
    ):
        with pytest.raises(ValueError):
            asyncio.run(etp_qa_map.preview(
                source, pages=pages, per_page=per_page, geo_limit=geo_limit
            ))


def test_blocked_profile_never_performs_qa_network_or_geocoding(monkeypatch):
    async def reject(*args, **kwargs):
        pytest.fail("blocked provider cannot be fetched")
    monkeypatch.setattr(etp_qa_map, "_collect", reject)
    report = asyncio.run(etp_qa_map.preview("rts-tender.ru"))
    assert report["status"] == "blocked"
    assert report["candidate_count"] == 0
    assert report["database_writes"] == 0
    assert not report["archival_enabled"]
    assert not report["complete_source_inventory"]


def test_listing_only_qa_report_keeps_exact_lot_provenance_but_has_no_fake_geo(monkeypatch):
    async def collect(*args):
        return [_lot()], 1, []
    monkeypatch.setattr(etp_qa_map, "_collect", collect)
    result = asyncio.run(etp_qa_map.preview("etpu.ru", geo_limit=0))
    assert result["qa_only"] is True
    assert result["verified_for_production"] is False
    assert result["candidate_count"] == 1
    assert result["mapped_count"] == 0
    assert result["items"][0]["geo_status"] == "unresolved"
    assert result["items"][0]["unverified"] is True
    assert result["items"][0]["url"].startswith("https://bankrupt.etpu.ru/")


def test_foreign_links_cannot_enter_manual_qa_map(monkeypatch):
    async def collect(*args):
        return [_lot(lot_url="https://evil.example/public/auctions/lots/view/123/")], 1, []
    monkeypatch.setattr(etp_qa_map, "_collect", collect)
    result = asyncio.run(etp_qa_map.preview("etpu.ru", geo_limit=0))
    assert result["candidate_count"] == 1
    assert result["items"] == []
    assert result["mapped_count"] == 0


def test_geo_result_requires_exact_cadastral_identity_and_valid_coordinates():
    point = SimpleNamespace(
        cadastral_number="50:05:0010203:1234", lat=55.8, lon=37.7
    )
    assert etp_qa_map._valid_geo_result(point, point.cadastral_number)
    point.cadastral_number = "50:05:0010203:1235"
    assert not etp_qa_map._valid_geo_result(point, "50:05:0010203:1234")
    point.cadastral_number = "50:05:0010203:1234"
    point.lat = float("nan")
    assert not etp_qa_map._valid_geo_result(point, point.cadastral_number)


def test_all_source_batch_cannot_claim_production_import(monkeypatch, capsys):
    from bankrotai.services import etp_qa_batch

    async def stub(source, **kwargs):
        return {
            "status": "blocked" if source == "rts-tender.ru" else "preview",
            "pages_fetched": 0, "candidate_count": 0, "mapped_count": 0,
            "errors": [],
        }
    monkeypatch.setattr(etp_qa_batch, "preview", stub)
    report = asyncio.run(etp_qa_batch.run_all(
        pages=1, per_page=1, geo_limit=0
    ))
    assert report["source_count"] == len(etp_qa_map.SUPPORTED)
    assert report["source_count"] == 21
    assert report["database_writes"] == 0
    assert report["production_map_writes"] == 0
    assert report["archival_enabled"] is False
    assert report["complete_source_inventory"] is False
    progress = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(progress) == 21
    assert progress[0]["completed_sources"] == 1
    assert progress[0]["total_sources"] == 21
    assert progress[-1]["completed_sources"] == 21
    assert progress[-1]["percent"] == 100.0
