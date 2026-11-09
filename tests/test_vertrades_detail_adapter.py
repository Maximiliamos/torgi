"""No-network integration checks for Vertrades exact-lot detail enrichment."""
from __future__ import annotations

import asyncio
import pytest
import requests

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES
from bankrotai.connectors.etp_sources.common import (
    HtmlEtpConnector,
    SourceAccessError,
    SourceSchemaError,
)
from bankrotai.connectors.etp_sources.profiles import PROFILES
from bankrotai.connectors.etp_sources.vertrades import (
    enrich_vertrades_lot,
    fetch_vertrades_lot,
)
from bankrotai.domain import NormalizedLot
from bankrotai.connectors.registry import connector_registry

PROFILE = next(p for p in PROFILES if p.source_id == "vertrades.ru")


class DirectVertradesConnector(HtmlEtpConnector):
    profile = PROFILE


URL = "https://arest.vertrades.ru/showTrades?id=102259678"


def html(*, lot2_price: str = "246 800,00", procedure: str = "55-АП") -> bytes:
    return f"""
    <html><body>
      <h3>идентификационный номер: {procedure}</h3>
      <table>
        <tr><td>Начало предоставления заявок на участие</td><td>11.08.2021 08:00</td></tr>
        <tr><td>Окончание предоставления заявок на участие</td><td>06.09.2021 23:59</td></tr>
        <tr><td>Начало подачи предложений о цене имущества</td><td>10.09.2021 10:00</td></tr>
      </table>
      <h3>Сведения по лоту №1</h3>
      <table>
        <tr><td>Статус торгов по лоту</td><td>Торги завершены</td></tr>
        <tr><td>Краткие сведения об имуществе (наименование лота)</td>
            <td>жилое здание пл.29,6кв.м. кад.№34:02:010003:2648</td></tr>
        <tr><td>Cведения об арестованном имуществе, реализуемом на торгах</td>
            <td>ЛОТ№1 жилое здание пл.29,6кв.м. кад.№34:02:010003:2648.
            Адрес: г. Волгоград, ул. Тестовая, д.1. Начальная цена: 1 060 000руб.
            Задаток: 50 000руб. Ограничения: запрет регистрации.</td></tr>
        <tr><td>Начальная цена продажи имущества</td><td>1 060 000,00 НДС не облагается</td></tr>
        <tr><td>Размер задатка</td><td>50 000,00</td></tr>
      </table>
      <h3>Сведения по лоту №2</h3>
      <table>
        <tr><td>Статус торгов по лоту</td><td>Торги по лоту отменены</td></tr>
        <tr><td>Краткие сведения об имуществе (наименование лота)</td>
            <td>нежилое здание пл.9кв.м. кад.№34:34:080147:2677</td></tr>
        <tr><td>Cведения об арестованном имуществе, реализуемом на торгах</td>
            <td>ЛОТ№2 нежилое здание пл.9кв.м. кад.№34:34:080147:2677.
            Адрес: г. Волгоград, ул. Тестовая, д.2. Начальная цена: {lot2_price}руб.
            Задаток: 20 000руб. Ограничения: запрет регистрации.</td></tr>
        <tr><td>Начальная цена продажи имущества</td><td>{lot2_price} НДС не облагается</td></tr>
        <tr><td>Размер задатка</td><td>20 000,00</td></tr>
      </table>
    </body></html>
    """.encode("utf8")


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.url = URL

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.headers = {}
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append((url, kwargs.get("allow_redirects")))
        return self.response


def listing(*, lot: int = 2, price: float = 246_800.0, procedure: str = "55-АП") -> NormalizedLot:
    return NormalizedLot(
        external_id=f"vertrades.ru:102259678:{lot}",
        source="vertrades", source_system="vertrades.ru",
        title="нежилое здание", description="нежилое здание",
        category="real_estate", region_slug=None, region_name=None,
        address=None,
        cadastral_number="34:34:080147:2677" if lot == 2 else "34:02:010003:2648",
        vin=None, area=None, start_price=price, current_price=None,
        auction_status="closed", lot_url=URL, source_url=URL, detail_level="search",
        raw_data={"trade_id": procedure, "lot_number": str(lot)},
    )


def test_fetch_lot_selects_requested_section_only():
    session = FakeSession(FakeResponse(html()))
    connector = DirectVertradesConnector(session=session)
    one = asyncio.run(fetch_vertrades_lot(connector, "vertrades.ru:102259678:1"))
    two = asyncio.run(fetch_vertrades_lot(connector, "vertrades.ru:102259678:2"))
    assert one.external_id != two.external_id
    assert one.start_price == 1_060_000.0
    assert two.start_price == 246_800.0
    assert one.cadastral_number == "34:02:010003:2648"
    assert two.cadastral_number == "34:34:080147:2677"
    assert one.auction_status == two.auction_status == "closed"
    assert two.procedure_number == "55-АП"
    assert two.application_deadline.isoformat() == "2021-09-06T23:59:00"
    assert two.auction_at.isoformat() == "2021-09-10T10:00:00"
    assert session.requested == [(URL, False), (URL, False)]


def test_enrichment_verifies_catalogue_identity_price_and_cadastre():
    session = FakeSession(FakeResponse(html()))
    connector = DirectVertradesConnector(session=session)
    item = listing()
    updated = asyncio.run(enrich_vertrades_lot(connector, item))
    assert updated.detail_level == "detail"
    assert updated.start_price == 246_800.0
    assert updated.deposit_amount == 20_000.0
    assert updated.raw_data["individual_lot_identity_verified"] is True
    assert updated.raw_data["detail_start_price_verified"] is True
    assert updated.raw_data["detail_status_verified"] is True
    assert updated.raw_data["detail_enrichment_status"] == "success"


def test_conflicting_catalogue_price_fails_closed_before_mutation():
    session = FakeSession(FakeResponse(html()))
    connector = DirectVertradesConnector(session=session)
    item = listing(price=20_000.0)
    with pytest.raises(SourceSchemaError, match="listing and individual detail price differ"):
        asyncio.run(enrich_vertrades_lot(connector, item))
    assert item.detail_level == "search"


def test_wrong_procedure_or_lot_identity_fails_closed():
    connector = DirectVertradesConnector(session=FakeSession(FakeResponse(html())))
    with pytest.raises(SourceSchemaError, match="procedure code mismatch"):
        asyncio.run(enrich_vertrades_lot(connector, listing(procedure="999-АП")))
    with pytest.raises(SourceSchemaError, match="requested_lot_missing"):
        asyncio.run(fetch_vertrades_lot(connector, "vertrades.ru:102259678:3"))


def test_redirect_is_refused_by_direct_source_adapter():
    connector = DirectVertradesConnector(
        session=FakeSession(FakeResponse(html(), status_code=302))
    )
    with pytest.raises(SourceAccessError, match="provider redirect was refused"):
        asyncio.run(fetch_vertrades_lot(connector, "vertrades.ru:102259678:2"))



def _catalogue_html() -> bytes:
    filler = "".join("<td>-</td>" for _ in range(4))
    return (
        "<html><body><table><tr>"
        "<td>55-АП</td><td>2</td><td>synthetic debtor</td>"
        "<td><a href='/showTrades?id=102259678'>"
        "нежилое здание 9 кв.м. кадастровый номер 34:34:080147:2677"
        "</a></td><td>246800.00</td>"
        + filler +
        "<td>Завершенные торги</td>"
        "</tr></table></body></html>"
    ).encode("utf-8")


def test_registered_connector_uses_verified_source_specific_contract():
    connector_cls = SOURCE_CONNECTOR_TYPES["vertrades.ru"]
    assert {"search", "fetch_lot", "healthcheck", "detail_enrichment"}.issubset(
        connector_cls.capabilities
    )
    registered = connector_registry.create("vertrades.ru")
    assert type(registered).__name__ == "VertradesConnector"
    assert registered.capabilities == connector_cls.capabilities

    catalogue_session = FakeSession(FakeResponse(_catalogue_html()))
    catalogue_connector = connector_cls(session=catalogue_session)
    page = asyncio.run(catalogue_connector.search({}))
    assert page.next_cursor is None
    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "vertrades.ru:102259678:2"
    assert item.raw_data["trade_id"] == "55-АП"
    assert item.raw_data["lot_number"] == "2"
    assert item.start_price == 246_800.0

    detail_connector = connector_cls(session=FakeSession(FakeResponse(html())))
    detail = asyncio.run(detail_connector.fetch_lot(item.external_id))
    assert detail.external_id == item.external_id
    assert detail.start_price == 246_800.0
    assert detail.auction_status == "closed"
