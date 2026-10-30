"""Read-only CDTRF API and exact-lot validation: no real network calls."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bankrotai.services.etp_cdtrf_canary import (
    CdtrfCanaryError, create_cdtrf_canary_router,
    fetch_verified_cdtrf_lot, preview_cdtrf_public_api,
)


class Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    content = b'{"success":true}'

    def json(self):
        return {"success": True, "totalCount": 1, "items": [
            {"tradeId": 390364, "tradeLotId": 390899,
             "categories": ",21,", "regionId": 32,
             "actualPrice": 675000, "tradeStatusDescription": "Объявлены"},
        ]}


class Session:
    def get(self, url, *, allow_redirects, timeout, headers):
        assert "torgi.cdtrf.ru/api/Trade/trades?" in url
        assert "PageNum=1" in url
        assert allow_redirects is False
        return Response()


def test_api_preview_returns_real_estate_lot_identity_not_debtor():
    report = preview_cdtrf_public_api(1, session=Session())
    assert report["candidate_count"] == 1
    row = report["candidates"][0]
    assert row["trade_id"] == "390364"
    assert row["trade_lot_id"] == "390899"
    assert row["title"] is None
    assert row["ready_for_ingestion"] is False
    assert report["database_writes"] == 0
    assert report["complete_source_inventory"] is False


def test_rejects_unbounded_pages_and_untrusted_lot_ids():
    with pytest.raises(CdtrfCanaryError):
        preview_cdtrf_public_api(6, session=Session())
    with pytest.raises(CdtrfCanaryError):
        fetch_verified_cdtrf_lot("https://evil.example", "390899")
    with pytest.raises(CdtrfCanaryError):
        fetch_verified_cdtrf_lot("390364", "wrong")


def test_exact_detail_verified_against_requested_trade_lot(monkeypatch):
    import bankrotai.services.etp_cdtrf_canary as module
    def fake_get(session, url, *, accepts):
        assert url == "https://torgi.cdtrf.ru/trades/390364"
        return SimpleNamespace(headers={"Content-Type": "text/html"}, text="<html></html>")
    def fake_parse(html, url):
        return {
            "source": "cdtrf.ru", "trade_id": "390364",
            "trade_lot_id": "390899", "title": "Земельный участок, кадастр 32:10:0010101:42",
            "cadastral_number": "32:10:0010101:42",
            "initial_price_rub": "675000.00", "source_status_code": "Declare",
            "source_status_text": "Торги объявлены", "region_label": "Брянская область",
            "region_id": 32, "price_periods": [],
        }
    monkeypatch.setattr(module, "_http_get", fake_get)
    monkeypatch.setattr(module, "parse_detail", fake_parse)
    lot = fetch_verified_cdtrf_lot("390364", "390899", session=object())
    assert lot.external_id == "cdtrf:390899"
    assert lot.category == "land"
    assert lot.current_price is None
    assert lot.cadastral_number == "32:10:0010101:42"
    assert lot.auction_status == "scheduled"
    with pytest.raises(CdtrfCanaryError, match="differs"):
        fetch_verified_cdtrf_lot("390364", "390898", session=object())
    with pytest.raises(CdtrfCanaryError, match="CDTRF_CADASTRAL_MISSING"):
        monkeypatch.setattr(module, "parse_detail", lambda h, u: {**fake_parse(h,u), "cadastral_number": None})
        fetch_verified_cdtrf_lot("390364", "390899", session=object())


def test_http_api_routes_require_optin_and_no_db_mutations(monkeypatch):
    def require_admin():
        return SimpleNamespace(id=1, role="admin", username="test")
    app = FastAPI()
    app.include_router(create_cdtrf_canary_router(require_admin))
    monkeypatch.delenv("ETP_CDTRF_CANARY_IMPORT_ENABLED", raising=False)
    with TestClient(app) as client:
        blocked = client.post("/api/etp/pilots/cdtrf.ru/import/390364/390899")
    assert blocked.status_code == 403



def test_generic_cdtrf_heading_uses_actual_property_description(monkeypatch):
    """Real CDTRF headings often name a debtor, while lotInfo names the land."""
    import bankrotai.services.etp_cdtrf_canary as module

    monkeypatch.setattr(
        module, "_http_get",
        lambda session, url, *, accepts: SimpleNamespace(
            headers={"Content-Type": "text/html"}, text="<html></html>"
        ),
    )
    detail = {
        "trade_id": "390364", "trade_lot_id": "390899",
        "title": "Торги по реализации имущества должника",
        "property_description": (
            "Лот № 1 — земельный участок, кадастровый номер 32:10:0010101:42, "
            "площадь 600 кв. м, в собственности"
        ),
        "category_label": "Земельные участки",
        "cadastral_number": "32:10:0010101:42",
        "initial_price_rub": "675000.00", "source_status_code": "RecieveReq",
        "source_status_text": "Прием заявок", "region_label": "Брянская область",
        "region_id": 32, "price_periods": [],
    }
    monkeypatch.setattr(module, "parse_detail", lambda html, url: detail)
    lot = fetch_verified_cdtrf_lot("390364", "390899", session=object())
    assert lot.category == "land"
    assert lot.description.startswith("Лот № 1")
    assert lot.auction_status == "active"


@pytest.mark.parametrize("bad_description", [
    "Лот 1 — 1/5 доля в праве собственности на земельный участок, "
    "кадастровый номер 32:10:0010101:42",
    "Лот 1 — право аренды земельного участка, "
    "кадастровый номер 32:10:0010101:42",
    "Лот 1 — право требования по договору на объект недвижимости, "
    "кадастровый номер 32:10:0010101:42",
])
def test_cdtrf_rights_are_not_misclassified_as_real_estate(
    monkeypatch, bad_description,
):
    import bankrotai.services.etp_cdtrf_canary as module

    monkeypatch.setattr(
        module, "_http_get",
        lambda session, url, *, accepts: SimpleNamespace(
            headers={"Content-Type": "text/html"}, text="<html></html>"
        ),
    )
    monkeypatch.setattr(module, "parse_detail", lambda html, url: {
        "trade_id": "390364", "trade_lot_id": "390899",
        "title": "Реализация имущества", "property_description": bad_description,
        "category_label": "Недвижимое имущество",
        "cadastral_number": "32:10:0010101:42",
        "initial_price_rub": "675000.00", "source_status_code": "Declare",
    })
    with pytest.raises(CdtrfCanaryError, match="UNSUPPORTED_PROPERTY_RIGHT"):
        fetch_verified_cdtrf_lot("390364", "390899", session=object())


def test_lotinfo_is_extracted_as_property_text():
    from bankrotai.connectors.registry.cdtrf_public import _lot_property_text

    text = _lot_property_text({
        "name": "Торги по реализации имущества должника",
        "lotInfo": "<p>Земельный участок, кадастровый номер 32:10:0010101:42</p>",
        "description": None,
    })
    assert text == "Земельный участок, кадастровый номер 32:10:0010101:42"



@pytest.mark.parametrize("raw_status,expected", [
    ("Declare", "scheduled"), ("RecieveReq", "active"),
    ("DefinePart", "scheduled"), ("TradeGo", "active"),
    ("SummingUp", "closed"),
])
def test_cdtrf_all_five_stages_are_normalized(monkeypatch, raw_status, expected):
    import bankrotai.services.etp_cdtrf_canary as module

    monkeypatch.setattr(module, "_http_get", lambda *a, **k: SimpleNamespace(
        headers={"Content-Type": "text/html"}, text="<html></html>"
    ))
    monkeypatch.setattr(module, "parse_detail", lambda *a, **k: {
        "trade_id": "390364", "trade_lot_id": "390899",
        "title": "Земельный участок 32:10:0010101:42",
        "property_description": "Земельный участок, кадастр 32:10:0010101:42",
        "category_label": "Земельный участок",
        "cadastral_number": "32:10:0010101:42",
        "initial_price_rub": "675000.00",
        "source_status_code": raw_status,
    })
    result = module.fetch_verified_cdtrf_lot(
        "390364", "390899", session=object()
    )
    assert result.auction_status == expected


def test_cdtrf_operator_source_is_disabled_without_three_gates(monkeypatch):
    from bankrotai.services.ingestion import source_full_specs, default_source_specs

    for name in (
        "CDTRF_OPERATOR_SYNC_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert "cdtrf.ru" not in [x.source_id for x in default_source_specs()]
    with pytest.raises(PermissionError):
        source_full_specs("cdtrf.ru")
    for name in (
        "CDTRF_OPERATOR_SYNC_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED",
    ):
        monkeypatch.setenv(name, "1")
    (spec,) = source_full_specs("cdtrf.ru")
    assert spec.source_id == "cdtrf.ru"
    assert spec.reconcile_missing is False
    assert spec.filters.page_size == 100
    assert spec.max_batches == 10000



def test_cdtrf_source_full_sync_traverses_all_pages_without_archiving(monkeypatch):
    """Connector API real path, fake requests and detail; 3 pages of 100."""
    from urllib.parse import parse_qs, urlsplit
    from bankrotai.connectors.registry.direct_etp_pilots import DirectEtpPilotConnector
    from bankrotai.connectors.registry.cdtrf_sync import CdtrfSyncFilters

    class R:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b"{}"
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return self.payload

    class S:
        def get(self, url, *, timeout, allow_redirects, headers):
            q = parse_qs(urlsplit(url).query)
            assert q["PageSize"] == ["100"]
            p = int(q["PageNum"][0])
            return R({
                "success": True, "totalCount": 205,
                "items": [
                    {"tradeId": 390000 + i, "tradeLotId": 500000 + i,
                     "categories": ",21,", "actualPrice": 120000}
                    for i in range((p - 1) * 100 + 1, min(205, p * 100) + 1)
                ],
            })
    import bankrotai.connectors.registry.cdtrf_sync as mod
    def fake_detail(trade, lot, *, session):
        assert trade.isdigit() and lot.isdigit()
        from bankrotai.domain import NormalizedLot
        return NormalizedLot(
            external_id=f"cdtrf:{lot}", source="cdtrf.ru",
            source_system="cdtrf.ru", title="Земельный участок",
            description="Участок 32:10:0010101:42", category="land",
            region_slug=None, region_name=None, address=None,
            cadastral_number="32:10:0010101:42", vin=None, area=None,
            start_price=120000, current_price=None,
            auction_status="active",
            lot_url=f"https://torgi.cdtrf.ru/trades/{trade}",
            source_url=None, detail_level="public_detail", raw_data={},
        )
    monkeypatch.setattr(mod, "fetch_verified_cdtrf_lot", fake_detail)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    connector = DirectEtpPilotConnector("cdtrf.ru", session=S())
    filters = CdtrfSyncFilters(page_size=100)
    cursor = None
    seen = set()
    for page_number in (1, 2, 3):
        result = asyncio.run(connector.search(filters, cursor))
        assert result.metadata["progress_current"] == page_number
        assert result.metadata["progress_total"] == 3
        assert result.metadata["safe_for_archival"] is False
        assert result.metadata["complete_source_inventory"] is False
        ids = {x.external_id for x in result.items}
        assert not ids & seen
        seen.update(ids)
        cursor = result.next_cursor
    assert len(seen) == 205
    assert cursor is None
