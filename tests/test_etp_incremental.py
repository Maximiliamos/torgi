"""Offline contract checks for bounded M-ETS and CDTRF catalogue ingestion."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bankrotai.connectors.base import ConnectorPage, ConnectorCapabilityError
from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import (
    EtpIncrementalFilters, incremental_cdtrf_search, incremental_mets_search,
)
from bankrotai.services.etp_incremental_preview_api import create_etp_incremental_preview_router

FIRST = '<html><a href="243003-1">Жилой дом</a><a href="242999-1">Участок</a></html>'
SECOND = '<html><a href="242994-1">Нежилое помещение</a><a href="242989-1">Дом</a></html>'


def fake_lot(source, external_id):
    return SimpleNamespace(
        source_system=source, external_id=external_id,
        title="Жилой дом, кадастр 50:18:0080111:1897", category="house",
        cadastral_number="50:18:0080111:1897",
        start_price=5948100.0, current_price=None,
        auction_status="scheduled",
        lot_url=f"https://m-ets.ru/{external_id}" if source == "m-ets.ru"
        else "https://torgi.cdtrf.ru/trades/390364",
        raw_data={}, description="Дом",
    )


class FakePilot:
    def __init__(self):
        self.calls = []
        self._last_request_at = None

    def _get(self, url):
        self.calls.append(url)
        if url.endswith("243003-1") or url.endswith("242999-1"):
            return "detail"
        return FIRST


class Page2Response:
    status_code = 200
    headers = {"Content-Type": "text/html"}
    text = SECOND
    content = SECOND.encode()


class FakeSession:
    def get(self, url, *, timeout, allow_redirects, headers):
        assert url.endswith("&page=2")
        assert not allow_redirects
        return Page2Response()


def test_mets_two_pages_prove_distinct_identities_no_archival(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    monkeypatch.setattr(mod, "parse_mets_lot",
                        lambda html, url: fake_lot("m-ets.ru", url.rsplit("/", 1)[-1]))
    p = FakePilot()
    p.session = FakeSession()
    filters = EtpIncrementalFilters("m-ets.ru", max_detail_requests=1)
    first = incremental_mets_search(p, filters, None)
    assert first.next_cursor is not None
    assert len(first.items) == 1
    assert first.metadata["complete_source_inventory"] is False
    assert first.metadata["safe_for_archival"] is False
    second = incremental_mets_search(p, filters, first.next_cursor)
    assert second.next_cursor is None
    assert second.items[0].external_id == "242994-1"
    assert first.items[0].external_id != second.items[0].external_id


def test_mets_rejects_repeated_catalogue_and_untrusted_cursor(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    monkeypatch.setattr(mod, "parse_mets_lot",
                        lambda html, url: fake_lot("m-ets.ru", url.rsplit("/", 1)[-1]))
    pilot = FakePilot()
    class RepeatSession(FakeSession):
        def get(self, *args, **kwargs):
            resp = Page2Response()
            resp.text, resp.content = FIRST, FIRST.encode()
            return resp
    pilot.session = RepeatSession()
    filters = EtpIncrementalFilters("m-ets.ru")
    first = incremental_mets_search(pilot, filters, None)
    with pytest.raises(Exception, match="repeats"):
        incremental_mets_search(pilot, filters, first.next_cursor)
    with pytest.raises(ValueError, match="cursor"):
        incremental_mets_search(pilot, filters, "999")


def test_cdtrf_api_identity_and_readonly_page_cursor(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    def preview(page, *, session=None):
        lot_id = "390899" if page == 1 else "390901"
        return {"candidates": [{"trade_id": "390364", "trade_lot_id": lot_id}]}
    def detail(trade, lot, *, session=None):
        assert trade == "390364"
        return fake_lot("cdtrf.ru", f"cdtrf:{lot}")
    monkeypatch.setattr(mod, "preview_cdtrf_public_api", preview)
    monkeypatch.setattr(mod, "fetch_verified_cdtrf_lot", detail)
    filters = EtpIncrementalFilters("cdtrf.ru", 1)
    a = incremental_cdtrf_search(filters, None)
    b = incremental_cdtrf_search(filters, a.next_cursor)
    assert [x.external_id for x in a.items] == ["cdtrf:390899"]
    assert [x.external_id for x in b.items] == ["cdtrf:390901"]
    assert b.next_cursor is None
    assert a.metadata["safe_for_archival"] is False


def test_pilots_reject_full_sync_and_other_sources():
    with pytest.raises(ValueError):
        EtpIncrementalFilters("rts-tender.ru")
    with pytest.raises(ValueError):
        EtpIncrementalFilters("m-ets.ru", 50)
    for source in ("m-ets.ru", "cdtrf.ru"):
        connector = connector_registry.create(source)
        with pytest.raises(ConnectorCapabilityError):
            asyncio.run(connector.search(None))


def test_admin_batch_preview_never_writes(monkeypatch):
    class Connector:
        async def search(self, filters, cursor=None):
            assert filters.max_detail_requests == 2
            return ConnectorPage(items=[fake_lot("cdtrf.ru", "cdtrf:390899")],
                                 next_cursor="2:0000000000000000",
                                 metadata={"catalogue_lots": 8, "details_rejected": 1})
    monkeypatch.setattr(connector_registry, "create", lambda source: Connector())
    app = FastAPI()
    app.include_router(create_etp_incremental_preview_router(
        lambda: SimpleNamespace(id=1, role="admin")
    ))
    with TestClient(app) as client:
        response = client.get("/api/etp/pilots/cdtrf.ru/batch-preview?limit=2")
        missing = client.get("/api/etp/pilots/rts-tender.ru/batch-preview")
        large = client.get("/api/etp/pilots/cdtrf.ru/batch-preview?limit=999")
    assert response.status_code == 200
    assert response.json()["database_writes"] == 0
    assert response.json()["archival_allowed"] is False
    assert response.json()["items"][0]["external_id"] == "cdtrf:390899"
    assert missing.status_code == 404
    assert large.status_code == 422


def test_production_readonly_middleware_allows_new_get_not_post():
    from bankrotai.api import _is_read_only_mvp_path
    def request(method, path):
        return SimpleNamespace(method=method, url=SimpleNamespace(path=path))
    assert _is_read_only_mvp_path(request("GET", "/api/etp/pilots/cdtrf.ru/batch-preview"))
    assert not _is_read_only_mvp_path(request("POST", "/api/etp/pilots/cdtrf.ru/batch-preview"))


def test_etp_optin_specs_never_allow_archiving_or_change_default_sources():
    from bankrotai.services.etp_incremental_specs import etp_incremental_source_specs
    from bankrotai.services.ingestion import default_source_specs

    specs = etp_incremental_source_specs()
    assert {s.source_id for s in specs} == {"m-ets.ru", "cdtrf.ru"}
    assert all(s.reconcile_missing is False and s.max_batches == 2 for s in specs)
    assert not {s.source_id for s in specs}.intersection(
        {s.source_id for s in default_source_specs()}
    )


def test_cdtrf_scans_next_candidate_when_first_detail_identity_fails(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod

    monkeypatch.setattr(
        mod, "preview_cdtrf_public_api",
        lambda page, session=None: {
            "candidates": [
                {"trade_id": "390384", "trade_lot_id": "390919"},
                {"trade_id": "390364", "trade_lot_id": "390899"},
            ]
        },
    )

    def inspect_detail(trade, lot, *, session=None):
        if lot == "390919":
            raise mod.CdtrfCanaryError("Detail lot ID differs from the requested lot")
        return fake_lot("cdtrf.ru", f"cdtrf:{lot}")

    monkeypatch.setattr(mod, "fetch_verified_cdtrf_lot", inspect_detail)
    result = mod.incremental_cdtrf_search(
        mod.EtpIncrementalFilters("cdtrf.ru", max_detail_requests=2), None,
    )
    assert [lot.external_id for lot in result.items] == ["cdtrf:390899"]
    assert result.metadata["details_rejected"] == 1
    assert result.metadata["detail_attempts"] == 2
    assert result.metadata["rejection_codes"] == {"lot_identity_mismatch": 1}
    assert result.metadata["safe_for_archival"] is False


def test_cdtrf_failure_classification_redacts_sensitive_error():
    from bankrotai.connectors.registry.etp_incremental import _cdtrf_rejection_code
    assert _cdtrf_rejection_code(ValueError("No CDTRF SSR data")) == "ssr_schema_changed"
    assert _cdtrf_rejection_code(ValueError("Price disagreement between HTML and SSR")) == "price_mismatch"
    assert _cdtrf_rejection_code(ValueError("Insufficient property with debtor Elena")) == "property_evidence_missing"
    assert _cdtrf_rejection_code(ValueError("sensitive person contact 7-900-000-0000")) == "other_detail_error"



def test_cdtrf_rejection_exposes_rights_risk_without_source_text():
    from bankrotai.connectors.registry.etp_incremental import _cdtrf_rejection_code
    from bankrotai.services.etp_cdtrf_canary import CdtrfCanaryError

    assert _cdtrf_rejection_code(
        CdtrfCanaryError("CDTRF_UNSUPPORTED_PROPERTY_RIGHT")
    ) == "unsupported_property_right"


def test_optin_five_page_mets_bounded_cursor_and_non_archival(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    monkeypatch.setattr(
        mod, "_get_mets_catalogue",
        lambda pilot, page: f'<html><a href="240000-{page}">Участок</a></html>',
    )
    monkeypatch.setattr(
        mod, "parse_mets_lot",
        lambda html, url: fake_lot("m-ets.ru", url.rsplit("/", 1)[-1]),
    )
    filters = EtpIncrementalFilters(
        "m-ets.ru", max_detail_requests=1, max_pages=5
    )
    cursor = None
    results = []
    for page in range(1, 6):
        result = incremental_mets_search(SimpleNamespace(), filters, cursor)
        assert result.metadata["progress_current"] == page
        assert result.metadata["progress_total"] == 5
        assert result.metadata["complete_source_inventory"] is False
        assert result.metadata["safe_for_archival"] is False
        results.extend(lot.external_id for lot in result.items)
        cursor = result.next_cursor
        if page < 5:
            assert cursor and cursor.startswith(f"{page + 1}:")
        else:
            assert cursor is None
    assert len(results) == len(set(results)) == 5
    with pytest.raises(ValueError, match="bounded"):
        incremental_mets_search(SimpleNamespace(), filters, "6:0000000000000000")


def test_optin_five_page_cdtrf_cursor_and_source_details(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    monkeypatch.setattr(
        mod, "preview_cdtrf_public_api",
        lambda page, session=None: {
            "candidates": [
                {"trade_id": "390364", "trade_lot_id": str(390899 + page)}
            ]
        },
    )
    monkeypatch.setattr(
        mod, "fetch_verified_cdtrf_lot",
        lambda trade, lot, session=None: fake_lot("cdtrf.ru", f"cdtrf:{lot}"),
    )
    filters = EtpIncrementalFilters(
        "cdtrf.ru", max_detail_requests=1, max_pages=5
    )
    cursor = None
    identities = []
    for page in range(1, 6):
        result = incremental_cdtrf_search(filters, cursor)
        identities.extend(item.external_id for item in result.items)
        assert result.metadata["progress_current"] == page
        assert result.metadata["progress_total"] == 5
        assert result.metadata["safe_for_archival"] is False
        assert result.metadata["complete_source_inventory"] is False
        cursor = result.next_cursor
    assert cursor is None
    assert len(set(identities)) == 5


def test_five_page_trial_is_explicit_optin_and_does_not_enable_archival():
    from bankrotai.services.etp_incremental_specs import (
        etp_five_page_source_specs, etp_incremental_source_specs,
    )
    from bankrotai.services.ingestion import default_source_specs

    specs = etp_five_page_source_specs()
    assert {s.source_id for s in specs} == {"m-ets.ru", "cdtrf.ru"}
    assert all(
        s.reconcile_missing is False
        and s.max_batches == 5
        and s.filters.max_pages == 5
        and s.filters.max_detail_requests == 2
        for s in specs
    )
    assert all(s.filters.max_pages == 2 for s in etp_incremental_source_specs())
    assert not {s.source_id for s in specs}.intersection(
        {s.source_id for s in default_source_specs()}
    )
    for pages in (0, 6):
        with pytest.raises(ValueError, match="bounded"):
            EtpIncrementalFilters("m-ets.ru", max_pages=pages)
