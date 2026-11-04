"""Read-only ETP diagnostic stage checklist regression tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bankrotai.services import etp_parser_checklist as checklist


def _lot(source: str, ident: str = "lot-1", **overrides):
    data = {
        "source_system": source,
        "external_id": ident,
        "start_price": 120000.0,
        "current_price": None,
        "auction_status": "active",
        "category": "land",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeConnector:
    def __init__(self, source: str, *, broken_second: bool = False,
                 duplicate: bool = False):
        self.source = source
        self.broken_second = broken_second
        self.duplicate = duplicate
        self.session = SimpleNamespace(close=lambda: None)

    async def search(self, filters, cursor=None):
        if cursor is None:
            return SimpleNamespace(
                items=[_lot(self.source)],
                next_cursor="2",
                metadata={"complete_source_inventory": False},
            )
        if self.broken_second:
            raise ValueError("changed listing schema")
        return SimpleNamespace(
            items=[_lot(self.source, "lot-1" if self.duplicate else "lot-2")],
            next_cursor=None,
            metadata={"complete_source_inventory": False},
        )

    async def fetch_lot(self, external_id):
        return _lot(self.source, external_id, detail_level="detail")


def test_registry_exactly_22_including_lot_online():
    sources = checklist.source_ids()
    assert len(sources) == 22
    assert len(set(sources)) == 22
    assert "lot-online.ru" in sources


def test_live_sample_exposes_unknown_terminal_page_without_claiming_import(monkeypatch):
    source = "etpu.ru"
    monkeypatch.setattr(
        checklist, "_connector",
        lambda s: (FakeConnector(s), object()),
    )
    result = asyncio.run(checklist.diagnose_source(source, pause_seconds=1.0))
    stages = result["stages"]
    assert result["pages_checked"] == 2
    assert result["sample_lots"] == 2
    assert stages["pagination_next"]["state"] == checklist.PASS
    assert stages["last_page_proven"]["state"] == checklist.NOT_TESTED
    assert stages["individual_detail"]["state"] == checklist.PASS
    assert stages["detail_identity"]["state"] == checklist.PASS
    assert stages["cookie_or_tutorial_modal"]["state"] == checklist.NOT_APPLICABLE
    assert result["production_approved"] is False
    assert result["database_writes"] == result["map_writes"] == 0


def test_second_page_failure_records_exact_stage_and_page(monkeypatch):
    monkeypatch.setattr(
        checklist, "_connector",
        lambda s: (FakeConnector(s, broken_second=True), object()),
    )
    item = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert item["first_failure"]["stage"] == "pagination_next"
    assert item["first_failure"]["page"] == 2
    assert item["stages"]["pagination_next"]["state"] == checklist.FAIL


def test_cross_page_duplicate_rejected(monkeypatch):
    monkeypatch.setattr(
        checklist, "_connector",
        lambda s: (FakeConnector(s, duplicate=True), object()),
    )
    item = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert item["first_failure"]["stage"] == "stable_lot_ids"
    assert item["stages"]["stable_lot_ids"]["state"] == checklist.FAIL


def test_blocked_profile_does_not_attempt_network(monkeypatch):
    def should_not_connect(_source):
        raise AssertionError("blocked profile must not make a request")
    monkeypatch.setattr(checklist, "_connector", should_not_connect)
    item = asyncio.run(checklist.diagnose_source("rts-tender.ru"))
    assert item["stages"]["transport"]["state"] == checklist.BLOCKED
    assert item["sample_lots"] == 0
    assert item["database_writes"] == 0


def test_bounded_args_reject_unsafe_scan():
    for kwargs in ({"max_pages": 3}, {"detail_probes": 3}, {"pause_seconds": 0}):
        try:
            asyncio.run(checklist.diagnose_source("etpu.ru", **kwargs))
            assert False, kwargs
        except ValueError:
            pass


def test_fast_source_is_bounded_without_terminal_requirement():
    from bankrotai.services.ingestion import (
        default_source_specs, fast_source_specs,
    )
    full = next(x for x in default_source_specs()
                if x.source_id == "torgi-russia.ru")
    fast = next(x for x in fast_source_specs(gis_publish_date_from="2026-11-01")
                if x.source_id == "torgi-russia.ru")
    assert full.require_source_end is True
    assert full.reconcile_missing is False
    assert fast.max_batches == 1
    assert fast.require_source_end is False
    assert fast.reconcile_missing is False


def test_diagnose_all_progress_includes_per_stage_state(monkeypatch, capsys):
    monkeypatch.setattr(
        checklist, "_connector",
        lambda source: (FakeConnector(source, broken_second=True), object()),
    )
    result = asyncio.run(checklist.diagnose_all(
        ("etpu.ru",), max_pages=2, detail_probes=0, pause_seconds=1.0,
    ))
    import json
    line = capsys.readouterr().out.strip().splitlines()[-1]
    progress = json.loads(line)
    assert progress["source"] == "etpu.ru"
    assert progress["failure"]["stage"] == "pagination_next"
    assert progress["stage_states"]["pagination_next"] == checklist.FAIL
    assert progress["pages_checked"] == 1
    assert progress["sample_lots"] == 1
    assert result["database_writes"] == result["map_writes"] == 0
