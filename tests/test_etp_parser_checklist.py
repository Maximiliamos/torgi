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
    capabilities = frozenset({"search", "fetch_lot"})

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


def test_http_diagnostic_exposes_only_safe_status_and_no_urls(monkeypatch):
    from bankrotai.connectors.etp_sources import SourceAccessError

    class Blocked(FakeConnector):
        async def search(self, filters, cursor=None):
            raise SourceAccessError("Provider returned HTTP 429 at https://private.example/?token=SECRET")

    monkeypatch.setattr(checklist, "_connector", lambda source: (Blocked(source), object()))
    report = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert report["stages"]["transport"]["state"] == checklist.BLOCKED
    assert report["first_failure"]["category"] == "provider_http_429"
    assert report["first_failure"]["http_status"] == 429
    assert report["stages"]["transport"]["exception_class"] == "SourceAccessError"
    assert "SECRET" not in str(report)
    assert "private.example" not in str(report)


def test_missing_detail_capability_is_not_a_false_parser_failure(monkeypatch):
    class ListingOnly(FakeConnector):
        capabilities = frozenset({"search"})

    monkeypatch.setattr(checklist, "_connector", lambda source: (ListingOnly(source), object()))
    report = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert report["stages"]["individual_detail"]["state"] == checklist.NOT_APPLICABLE
    assert report["stages"]["detail_identity"]["state"] == checklist.NOT_APPLICABLE
    assert report["first_failure"] is None


def test_enrichment_capability_used_instead_of_missing_fetch_lot(monkeypatch):
    class EnrichmentOnly(FakeConnector):
        capabilities = frozenset({"search", "detail_enrichment"})
        async def enrich_lot(self, lot):
            lot.detail_level = "detail"
            return lot
        async def fetch_lot(self, external_id):
            raise AssertionError("must not call unadvertised fetch_lot")

    monkeypatch.setattr(checklist, "_connector", lambda source: (EnrichmentOnly(source), object()))
    report = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert report["stages"]["individual_detail"]["state"] == checklist.PASS
    assert report["stages"]["detail_identity"]["state"] == checklist.PASS


def test_identity_mismatch_never_counts_as_validated_detail(monkeypatch):
    class WrongIdentity(FakeConnector):
        async def fetch_lot(self, external_id):
            return _lot(self.source, "wrong-id", detail_level="detail")

    monkeypatch.setattr(checklist, "_connector", lambda source: (WrongIdentity(source), object()))
    report = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert report["stages"]["detail_identity"]["state"] == checklist.FAIL
    assert report["stages"]["individual_detail"]["state"] != checklist.PASS
    assert report["first_failure"]["stage"] == "detail_identity"


def test_cdtrf_checklist_uses_official_json_api_without_fake_html_failure(monkeypatch):
    from bankrotai.services import etp_cdtrf_pagination_probe

    called = {}
    def fake_scan(session, *, max_pages, delay_seconds):
        called.update(pages=max_pages, delay=delay_seconds)
        return {
            "pages_scanned": 2,
            "property_hint_candidates": 4,
            "unique_catalogue_lot_ids": 40,
            "production_database_writes": 0,
            "archival_allowed": False,
        }

    monkeypatch.setattr(etp_cdtrf_pagination_probe, "scan_cdtrf_pages", fake_scan)
    def refuse_html(_source):
        raise AssertionError("legacy CDTRF HTML connector must not be queried")
    monkeypatch.setattr(checklist, "_connector", refuse_html)
    report = asyncio.run(checklist.diagnose_source("cdtrf.ru"))
    assert called["pages"] == 2
    assert report["catalogue_contract"] == "cdtrf_official_public_json"
    assert report["pages_checked"] == 2
    assert report["sample_lots"] == 4
    assert report["catalogue_rows_observed"] == 40
    assert report["stages"]["catalogue_markup"]["state"] == checklist.PASS
    assert report["stages"]["pagination_next"]["state"] == checklist.PASS
    assert report["stages"]["last_page_proven"]["state"] == checklist.NOT_TESTED
    assert report["stages"]["individual_detail"]["state"] == checklist.NOT_TESTED
    assert report["production_approved"] is False
    assert report["database_writes"] == report["map_writes"] == 0


def test_cdtrf_valid_json_without_property_candidates_is_not_false_failure(monkeypatch):
    from bankrotai.services import etp_cdtrf_pagination_probe

    monkeypatch.setattr(etp_cdtrf_pagination_probe, "scan_cdtrf_pages",
                        lambda *args, **kwargs: {
                            "pages_scanned": 1,
                            "property_hint_candidates": 0,
                            "unique_catalogue_lot_ids": 20,
                            "production_database_writes": 0,
                            "archival_allowed": False,
                        })
    report = asyncio.run(checklist.diagnose_source("cdtrf.ru"))
    assert report["stages"]["lot_discovery"]["state"] == checklist.NOT_TESTED
    assert report["stages"]["catalogue_markup"]["state"] == checklist.PASS
    assert report["first_failure"] is None


def test_cdtrf_official_json_failure_records_catalogue_stage(monkeypatch):
    from bankrotai.services import etp_cdtrf_pagination_probe

    def broken(*args, **kwargs):
        raise ValueError("wrong JSON contract private=SECRET")

    monkeypatch.setattr(etp_cdtrf_pagination_probe, "scan_cdtrf_pages", broken)
    report = asyncio.run(checklist.diagnose_source("cdtrf.ru"))
    assert report["first_failure"]["stage"] == "catalogue_markup"
    assert report["stages"]["catalogue_markup"]["state"] == checklist.FAIL
    assert "SECRET" not in str(report)


def test_detail_price_and_status_are_validated_independently(monkeypatch):
    class MissingMoney(FakeConnector):
        async def fetch_lot(self, external_id):
            return _lot(self.source, external_id, detail_level="detail",
                        start_price=None, current_price=None, auction_status="unknown")

    monkeypatch.setattr(checklist, "_connector", lambda source: (MissingMoney(source), object()))
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["initial_or_current_price"]["state"] == checklist.PASS
    assert result["stages"]["auction_status"]["state"] == checklist.PASS
    assert result["stages"]["individual_detail"]["state"] == checklist.PASS
    assert result["stages"]["individual_detail_price"]["state"] == checklist.FAIL
    assert result["stages"]["individual_detail_status"]["state"] == checklist.FAIL
    assert result["first_failure"]["stage"] == "individual_detail_price"


def test_enrichment_does_not_claim_independent_money_validation(monkeypatch):
    class Enrichment(FakeConnector):
        capabilities = frozenset({"search", "detail_enrichment"})
        async def enrich_lot(self, lot):
            lot.detail_level = "detail"
            return lot

    monkeypatch.setattr(checklist, "_connector", lambda source: (Enrichment(source), object()))
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["individual_detail"]["state"] == checklist.PASS
    assert result["stages"]["individual_detail_price"]["state"] == checklist.NOT_APPLICABLE
    assert result["stages"]["individual_detail_status"]["state"] == checklist.NOT_APPLICABLE


def test_terminal_page_with_next_cursor_is_fail_closed(monkeypatch):
    class Contradiction(FakeConnector):
        async def search(self, filters, cursor=None):
            return SimpleNamespace(
                items=[_lot(self.source)],
                next_cursor="2",
                metadata={"source_end_verified": True},
            )

    monkeypatch.setattr(checklist, "_connector", lambda source: (Contradiction(source), object()))
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["last_page_proven"]["state"] == checklist.FAIL
    assert result["first_failure"]["stage"] == "last_page_proven"
    assert result["first_failure"]["category"] == "terminal_page_has_next_cursor"
    assert result["database_writes"] == result["map_writes"] == 0


def test_partial_detail_success_is_never_green(monkeypatch):
    class PartiallyBroken(FakeConnector):
        async def fetch_lot(self, external_id):
            if external_id == "lot-2":
                raise ValueError("broken second detail")
            return _lot(self.source, external_id, detail_level="detail")

    monkeypatch.setattr(checklist, "_connector", lambda source: (PartiallyBroken(source), object()))
    result = asyncio.run(checklist.diagnose_source("etpu.ru", detail_probes=2))
    assert result["stages"]["individual_detail"]["state"] == checklist.FAIL
    assert result["stages"]["individual_detail_price"]["state"] == checklist.NOT_TESTED
    assert result["stages"]["individual_detail_status"]["state"] == checklist.NOT_TESTED
    assert result["first_failure"]["stage"] == "individual_detail"
