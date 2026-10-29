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
