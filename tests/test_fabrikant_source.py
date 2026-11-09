from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import requests

from bankrotai.connectors.etp_sources.common import EtpSourceProfile, SourceSchemaError
from bankrotai.connectors.etp_sources.fabrikant import (
    external_id_from_lot_url,
    parse_detail_lot,
    procedure_urls,
)
from bankrotai.connectors.etp_sources.fabrikant_connector import FabrikantConnector


FIXTURES = Path(__file__).parent / "fixtures" / "fabrikant"

FABRIKANT_PROFILE = EtpSourceProfile(
    source_id="fabrikant.ru",
    platform_name="Фабрикант",
    platform_code="fabrikant",
    listing_url=(
        "https://www.fabrikant.ru/procedure/search/sales"
        "?section_ids%5B%5D=6&page_limit=10"
    ),
    lot_url_pattern=(
        r"fabrikant\.ru/v2/trades/procedure/lot/view/"
        r"([^/?#]+)/([^/?#]+)/?"
    ),
    page_parameter="page_number",
    supports_get_pagination=True,
    allowed_hosts=("fabrikant.ru", "www.fabrikant.ru"),
)


class IsolatedFabrikantConnector(FabrikantConnector):
    profile = FABRIKANT_PROFILE


class FakeResponse:
    def __init__(self, content: bytes, url: str, status_code: int = 200) -> None:
        self.content = content
        self.url = url
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class RouteSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, *_args, **_kwargs) -> FakeResponse:
        self.requested_urls.append(url)
        try:
            return self.responses[url]
        except KeyError as exc:
            raise AssertionError(f"unexpected GET {url}") from exc


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_procedure_route_is_not_an_individual_fabrikant_lot() -> None:
    procedure = "https://www.fabrikant.ru/v2/trades/procedure/view/PROC_token-1"
    html = f'<a href="{procedure}">Земельный участок</a>'

    assert external_id_from_lot_url(procedure) is None
    assert procedure_urls(html, base_url=FABRIKANT_PROFILE.listing_url) == [procedure]


def test_fabrikant_lot_route_has_unambiguous_two_token_identity() -> None:
    href = (
        "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
        "PROC-token_1/LOT-token_2"
    )
    assert external_id_from_lot_url(href) == (
        "fabrikant.ru:PROC-token_1:LOT-token_2"
    )


def test_fabrikant_detail_reads_lot_price_status_and_property_fields() -> None:
    url = (
        "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
        "PROC-token_1/LOT-token_2"
    )
    lot = parse_detail_lot(
        FABRIKANT_PROFILE,
        fixture("detail_active.html"),
        detail_url=url,
    )

    assert lot.external_id == "fabrikant.ru:PROC-token_1:LOT-token_2"
    assert lot.start_price == 555_345
    assert lot.current_price is None
    assert lot.auction_status == "active"
    assert lot.category == "real_estate"
    assert lot.cadastral_number == "62:24:0010110:419"
    assert lot.area == 57.9
    assert lot.address == "Тестовая область, г. Тест, ул. Проверочная, д. 1"
    assert lot.application_deadline.isoformat() == "2026-08-12T10:00:00"
    assert lot.auction_at.isoformat() == "2026-08-13T11:00:00"
    assert lot.raw_data["detail_start_price_verified"] is True
    assert lot.raw_data["detail_status_verified"] is True


def test_fabrikant_public_offer_schedule_does_not_replace_lot_initial_price() -> None:
    lot = parse_detail_lot(
        FABRIKANT_PROFILE,
        fixture("detail_public_offer.html"),
        detail_url=(
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            "PUBLIC-OFFER/LOT-1"
        ),
    )
    assert lot.start_price == 45_000
    assert lot.current_price is None


def test_fabrikant_ended_status_is_explicit_not_application_date_inference() -> None:
    html = """
    <html><body>
      <h2>Лот №1. Нежилое помещение - Закончено (несостоявшаяся процедура)</h2>
      <div>Предмет договора Нежилое помещение площадью 19,2 м²</div>
      <div>Категория объекта Нежилые помещения</div>
      <div>Начальная цена предмета договора Цена без НДС:
      800 000,00 Рубль (RUB)</div>
      <div>Дата и время окончания приема заявок 01.07.2026 10:00</div>
    </body></html>
    """
    lot = parse_detail_lot(
        FABRIKANT_PROFILE,
        html,
        detail_url=(
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            "ENDED-PROC/ENDED-LOT"
        ),
    )
    assert lot.auction_status == "closed"
    assert lot.application_deadline.isoformat() == "2026-07-01T10:00:00"


def test_fabrikant_review_stage_remains_unknown_without_terminal_marker() -> None:
    html = """
    <html><body>
      <h2>Лот №2. Оборудование - Рассмотрение заявок и подведение итогов</h2>
      <div>Предмет договора Производственное оборудование</div>
      <div>Категория объекта Оборудование</div>
      <div>Начальная цена договора 100 000,00 Рубль (RUB)</div>
    </body></html>
    """
    lot = parse_detail_lot(
        FABRIKANT_PROFILE,
        html,
        detail_url=(
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            "REVIEW-PROC/REVIEW-LOT"
        ),
    )
    assert lot.auction_status == "unknown"


def test_fabrikant_catalogue_expands_procedure_to_individual_lot_detail() -> None:
    listing = FABRIKANT_PROFILE.listing_url
    procedure = "https://www.fabrikant.ru/v2/trades/procedure/view/PROC-A"
    detail = "https://www.fabrikant.ru/v2/trades/procedure/lot/view/PROC-A/LOT-A"

    session = RouteSession({
        listing: FakeResponse(
            fixture("catalogue_page.html").encode("utf-8"),
            listing,
        ),
        procedure: FakeResponse(
            fixture("procedure_page.html").encode("utf-8"),
            procedure,
        ),
        detail: FakeResponse(
            fixture("detail_active.html").encode("utf-8"),
            detail,
        ),
    })
    connector = IsolatedFabrikantConnector(session=session)

    result = asyncio.run(connector.search({}))

    assert result.next_cursor == "2"
    assert [lot.external_id for lot in result.items] == [
        "fabrikant.ru:PROC-A:LOT-A"
    ]
    assert result.items[0].start_price == 555_345
    assert result.items[0].auction_status == "active"
    assert result.items[0].raw_data["trade_id"] == "PROC-A"
    assert result.items[0].raw_data["lot_id"] == "LOT-A"
    assert session.requested_urls == [listing, procedure, detail]


def test_fabrikant_catalogue_fails_closed_when_procedure_has_no_lot_route() -> None:
    listing = FABRIKANT_PROFILE.listing_url
    procedure = "https://www.fabrikant.ru/v2/trades/procedure/view/PROC-ONLY"
    catalogue = f'<a href="{procedure}">Процедура</a>'.encode()
    procedure_html = (
        b"<html><body><h2>Lot data but no canonical lot link</h2></body></html>"
    )
    session = RouteSession({
        listing: FakeResponse(catalogue, listing),
        procedure: FakeResponse(procedure_html, procedure),
    })
    connector = IsolatedFabrikantConnector(session=session)

    with pytest.raises(SourceSchemaError, match="individual lot route"):
        asyncio.run(connector.search({}))
