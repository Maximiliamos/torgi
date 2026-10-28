"""Public M-ETS card regression tests; all HTTP is mocked (no live scrape in CI)."""
from __future__ import annotations

import pytest

from bankrotai.connectors.registry.mets_public import (
    APARTMENTS_URL,
    MetsEvidenceError,
    MetsPublicPilot,
    discover_lot_urls,
    parse_mets_lot,
)

URL = "https://meetings.m-ets.ru/240512-1"
CATALOGUE = """
<html><a href="/240512-1">Здание в Липецке</a>
<a href="https://meetings.m-ets.ru/240512-1">Repeat</a>
<a href="https://evil.example/111111-1">Not an ETP</a>
<a href="/238380-1">Квартира в Москве</a>
</html>
"""
DETAIL = """
<html lang="ru"><h1>240512-МЭТС</h1>
<h2>Здание в Липецке, 1240,8 кв.м.</h2>
<div>Лот 1</div>
<div>Идентификационный номер: 240512-МЭТС-1</div>
<div>22 500 000 ₽ Начальная цена</div>
<div>Объявленные торги</div>
<div>Начало приема заявок: 26.10.2026 09:00</div>
<div>Окончание приема заявок: 01.12.2026 15:00</div>
<div>Здание, площадь 1240,8 кв.м. Кадастровый номер: 48:20:0021101:37</div>
</html>
"""
DETAIL_PERIOD = """
<html><h1>238380-МЭТС</h1><h2>Квартира в Москве</h2>
<div>Идентификационный номер: 238380-МЭТС-1</div>
<div>3\u00a0436\u00a0617,64 ₽ Текущая цена</div>
<div>Торги в стадии приема заявок</div>
<div>Окончание приема заявок на периоде: 28.10.2026 12:00</div>
<div>Кадастровый номер: 77:09:0004006:11148</div></html>
"""


def test_discovery_only_allows_auction_lot_paths_on_official_host():
    assert discover_lot_urls(CATALOGUE) == [
        URL, "https://meetings.m-ets.ru/238380-1",
    ]
    assert discover_lot_urls(CATALOGUE, limit=1) == [URL]
    with pytest.raises(ValueError):
        discover_lot_urls(CATALOGUE, limit=1000)


def test_direct_etp_detail_price_status_identity_and_cadastre():
    lot = parse_mets_lot(DETAIL, URL)
    assert lot.external_id == "240512-1"
    assert lot.procedure_number == "240512-МЭТС"
    assert lot.title.startswith("Здание в Липецке")
    assert lot.cadastral_number == "48:20:0021101:37"
    assert lot.start_price == 22_500_000
    assert lot.current_price is None
    assert lot.auction_status == "scheduled"
    assert lot.application_deadline.isoformat() == "2026-12-01T15:00:00"
    assert lot.source_system == "m-ets.ru"
    assert lot.raw_data["evidence_scope"] == "pilot_non_exhaustive"


def test_period_is_not_the_final_application_deadline():
    lot = parse_mets_lot(
        DETAIL_PERIOD, "https://meetings.m-ets.ru/238380-1"
    )
    assert lot.current_price == 3436617.64
    assert lot.start_price is None
    assert lot.auction_status == "active"
    assert lot.application_deadline is None
    assert lot.raw_data["period_deadline"] == "2026-10-28T12:00:00"


@pytest.mark.parametrize("bad_url", [
    "http://meetings.m-ets.ru/240512-1",
    "https://evil.example/240512-1",
    "https://meetings.m-ets.ru/240512-1?q=secret",
])
def test_never_fetch_external_or_query_urls(bad_url):
    with pytest.raises(MetsEvidenceError):
        parse_mets_lot(DETAIL, bad_url)


def test_fail_closed_when_lot_identity_does_not_match_page():
    with pytest.raises(MetsEvidenceError, match="identifier"):
        parse_mets_lot(DETAIL, "https://meetings.m-ets.ru/240513-1")


class Response:
    def __init__(self, html, status_code=200):
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html"}

    def raise_for_status(self):
        return None


class SessionStub:
    def __init__(self, *, redirect=False):
        self.urls = []
        self.redirect = redirect

    def get(self, url, *, timeout, allow_redirects, headers):
        assert allow_redirects is False
        self.urls.append(url)
        if self.redirect:
            return Response("", status_code=302)
        if url == APARTMENTS_URL:
            return Response(CATALOGUE)
        if url == URL:
            return Response(DETAIL)
        if url == "https://meetings.m-ets.ru/238380-1":
            return Response("<html><h2>Broken card</h2></html>")
        raise AssertionError(f"Unexpected requested URL: {url}")


def test_pilot_is_bounded_and_never_claims_complete_coverage(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = SessionStub()
    result = MetsPublicPilot(session=session).preview(max_lots=2)
    assert result["discovered"] == 2
    assert result["parsed"] == 1
    assert result["failed"] == 1
    assert result["complete_source_inventory"] is False
    assert result["safe_for_archival"] is False
    assert result["written_to_database"] is False
    assert session.urls == [
        APARTMENTS_URL, URL, "https://meetings.m-ets.ru/238380-1",
    ]


def test_redirect_refused_without_following():
    with pytest.raises(MetsEvidenceError, match="Redirect"):
        MetsPublicPilot(session=SessionStub(redirect=True)).preview()


def test_registry_connector_fetches_exact_etp_lot_but_blocks_sync():
    import asyncio

    from bankrotai.connectors.base import ConnectorCapabilityError
    from bankrotai.connectors.registry import connector_registry
    from bankrotai.connectors.registry.mets_public import MetsConnector

    connector = MetsConnector(session=SessionStub())
    assert connector_registry.create("m-ets.ru").source_id == "m-ets.ru"
    got = asyncio.run(connector.fetch_lot("240512-1"))
    assert got.start_price == 22_500_000
    assert got.external_id == "240512-1"
    with pytest.raises(ConnectorCapabilityError):
        asyncio.run(connector.search({}))
