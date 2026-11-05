"""No-network NISTP per-lot fetch/enrichment integration checks."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import requests

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES, SourceAccessError, SourceSchemaError
from bankrotai.domain import NormalizedLot


URL = "https://nistp.ru/bankrot/trade_view.php?trade_nid=502957"


def shared_html(*, lot4_price: str = "3 274 000.00") -> bytes:
    """Only two provider-realistic tables from a shared ten-lot trade."""
    def lot(n: int, nid: int, cadastre: str, price: str) -> str:
        return f"""
        <table class='data' id='table_lot_{n}'>
          <thead><tr><th colspan=2 class='table_header'>
            <a name='lot{n}'></a><a name='lot{nid}'></a>Лот № {n}
          </th></tr></thead>
          <tbody>
          <tr><td class='label'>Номер лота</td><td>{n}</td></tr>
          <tr><td class='label'>Наименование имущества</td><td>Машино-место</td></tr>
          <tr><td class='label'>Cведения об имуществе (предприятии) должника,
              выставляемом на торги, его составе, характеристиках, описание</td>
              <td>Машино-место, площадью {14.3 if n == 4 else 13.3} кв. м,
              кад. № {cadastre}</td></tr>
          <tr><td class='label'>Местонахождение имущества</td><td>г. Москва</td></tr>
          <tr><td class='label'>Начальная цена</td><td>{price}</td></tr>
          <tr><td class='label'>Размер задатка, руб.</td><td>327 400.00</td></tr>
          <tr><td class='label'>Статус торгов</td><td>Торги объявлены</td></tr>
          </tbody></table>
        """

    return (
        "<html><body>"
        "<h1 class='node_header'>Купить лот через агента</h1>"
        "<h1 class='node_header'>ООО «Асмато» "
        "<span>Идентификационный номер</span> 72113-ОАОФ</h1>"
        + lot(4, 502967, "77:01:0004002:6614", lot4_price)
        + lot(5, 502969, "77:01:0004002:6633", "3 274 000.00")
        + "</body></html>"
    ).encode("utf8")


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.url = URL

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requested_urls: list[tuple[str, bool]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, **kwargs):
        self.requested_urls.append((url, kwargs["allow_redirects"]))
        return self.response


def listing(*, lot_number: int, price: float | None = None) -> NormalizedLot:
    return NormalizedLot(
        external_id=f"nistp.ru:502957:{lot_number}",
        source="nistp", source_system="nistp.ru",
        title="Машино-место", description="Машино-место",
        category="other", region_slug=None, region_name=None,
        address=None, cadastral_number=None, vin=None, area=None,
        start_price=price, current_price=None, auction_status="scheduled",
        lot_url=URL, source_url=URL, detail_level="search",
        raw_data={"trade_id": "72113-ОАОФ", "lot_number": str(lot_number)},
    )


def test_fetch_lot_4_and_5_get_correct_unique_card_data():
    session = FakeSession(FakeResponse(shared_html()))
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    four = asyncio.run(connector.fetch_lot("nistp.ru:502957:4"))
    five = asyncio.run(connector.fetch_lot("nistp.ru:502957:5"))
    assert four.external_id != five.external_id
    assert four.cadastral_number == "77:01:0004002:6614"
    assert five.cadastral_number == "77:01:0004002:6633"
    assert four.raw_data["native_lot_nid"] == "502967"
    assert five.raw_data["native_lot_nid"] == "502969"
    assert four.start_price == five.start_price == 3_274_000.0
    assert four.deposit_amount == five.deposit_amount == 327_400.0
    assert four.procedure_number == five.procedure_number == "72113-ОАОФ"
    assert four.detail_level == five.detail_level == "detail"
    assert session.requested_urls == [(URL, False), (URL, False)]


def test_enrich_preserves_lot_identity_and_source_price_provenance():
    session = FakeSession(FakeResponse(shared_html()))
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    item = listing(lot_number=4)
    updated = asyncio.run(connector.enrich_lot(item))
    assert updated.external_id == "nistp.ru:502957:4"
    assert updated.start_price == 3_274_000.0
    assert updated.cadastral_number == "77:01:0004002:6614"
    assert updated.deposit_amount == 327_400.0
    assert updated.raw_data["native_lot_nid"] == "502967"
    assert updated.raw_data["individual_lot_identity_verified"] is True
    assert updated.raw_data["detail_start_price_verified"] is True
    assert updated.raw_data["detail_enrichment_status"] == "success"


def test_conflicting_listing_price_is_rejected_without_mutation():
    session = FakeSession(FakeResponse(shared_html()))
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    item = listing(lot_number=4, price=327_400)
    with pytest.raises(SourceSchemaError, match="individual detail price differ"):
        asyncio.run(connector.enrich_lot(item))
    assert item.start_price == 327_400
    assert item.detail_level == "search"
    assert "detail_enrichment_status" not in item.raw_data


def test_different_trade_code_cannot_be_silently_accepted():
    session = FakeSession(FakeResponse(shared_html()))
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    item = listing(lot_number=4)
    item.raw_data["trade_id"] = "99999-ОАОФ"
    with pytest.raises(SourceSchemaError, match="procedure code mismatch"):
        asyncio.run(connector.enrich_lot(item))
    assert item.detail_level == "search"


def test_http_redirect_and_missing_lot_fail_before_ingestion():
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](
        session=FakeSession(FakeResponse(shared_html(), status_code=302))
    )
    with pytest.raises(SourceAccessError, match="provider redirect was refused"):
        asyncio.run(connector.fetch_lot("nistp.ru:502957:4"))
    session = FakeSession(FakeResponse(shared_html()))
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    with pytest.raises(ValueError, match="requested_lot_missing_in_trade_card"):
        asyncio.run(connector.fetch_lot("nistp.ru:502957:6"))
