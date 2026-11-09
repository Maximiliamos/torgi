from __future__ import annotations

import asyncio

import pytest
import requests

from bankrotai.connectors.etp_sources import (
    PROFILES,
    SOURCE_CONNECTOR_TYPES,
    SourceSchemaError,
    parse_detail_html,
    parse_listing_html,
)


def profile():
    return next(item for item in PROFILES if item.source_id == "fabrikant.ru")


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


def test_procedure_route_is_not_an_individual_fabrikant_lot() -> None:
    html = """
    <a href="https://www.fabrikant.ru/v2/trades/procedure/view/PROC_token-1">
      Земельный участок
    </a>
    """
    assert parse_listing_html(profile(), html) == []


def test_fabrikant_lot_route_has_unambiguous_two_token_identity() -> None:
    html = """
    <article>
      <a href="https://www.fabrikant.ru/v2/trades/procedure/lot/view/PROC-token_1/LOT-token_2">
        Земельный участок 50:01:001:123
      </a>
    </article>
    """
    lots = parse_listing_html(profile(), html)
    assert [lot.external_id for lot in lots] == [
        "fabrikant.ru:PROC-token_1:LOT-token_2"
    ]


def test_fabrikant_detail_reads_lot_price_status_and_property_fields() -> None:
    html = """
    <html><body>
      <h2>Лот №1. Квартира в тестовом городе - Прием заявок</h2>
      <div>Предмет договора Квартира общей площадью 57,90 м²,
      кадастровый номер 62:24:0010110:419.</div>
      <div>Категория объекта Квартира</div>
      <div>Характеристики объекта Кадастровый номер: 62:24:0010110:419
      Общая площадь, м²: 57,90</div>
      <div>Местонахождение объекта продажи Адрес: Тестовая область, г. Тест, ул. Проверочная, д. 1</div>
      <div>Начальная цена предмета договора Цена без НДС:
      555 345,00 Рубль (RUB)</div>
      <div>Размер задатка 111 069,00 Рубль (RUB)</div>
      <div>Шаг аукциона 27 767,25 Российский рубль (RUB)</div>
      <div>Дата и время начала приема заявок 07.07.2026 00:00</div>
      <div>Дата и время окончания приема заявок 12.08.2026 10:00</div>
      <div>Дата и время начала аукциона 13.08.2026 11:00</div>
      <p>Публичная индивидуальная карточка лота содержит достаточное описание.</p>
    </body></html>
    """
    url = (
        "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
        "PROC-token_1/LOT-token_2"
    )
    lot = parse_detail_html(profile(), html, detail_url=url)

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
    html = """
    <html><body>
      <h2>Лот №1. Земельный участок - Прием заявок</h2>
      <div>Предмет договора Земельный участок 1 000 м²</div>
      <div>Категория объекта Земельные участки</div>
      <div>Начальная цена предмета договора Цена без НДС:
      45 000,00 Рубль (RUB)</div>
      <div>Размер задатка 4 500,00 Рубль (RUB)</div>
      <h3>Этап понижения</h3>
      <div>Дата начала этапа приема заявок 03.11.2026 09:00
      Дата завершения этапа приема заявок 10.11.2026 09:00
      Начальная цена 9 000,00 Рубль (RUB)
      Размер задатка 450,00 Рубль (RUB)</div>
      <p>Публичная индивидуальная карточка лота содержит достаточное описание.</p>
    </body></html>
    """
    lot = parse_detail_html(
        profile(),
        html,
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
      <p>Публичная индивидуальная карточка лота содержит достаточное описание.</p>
    </body></html>
    """
    lot = parse_detail_html(
        profile(),
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
      <p>Публичная индивидуальная карточка лота содержит достаточное описание.</p>
    </body></html>
    """
    lot = parse_detail_html(
        profile(),
        html,
        detail_url=(
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            "REVIEW-PROC/REVIEW-LOT"
        ),
    )
    assert lot.auction_status == "unknown"


def test_fabrikant_catalogue_expands_procedure_to_individual_lot_detail() -> None:
    listing = "https://www.fabrikant.ru/procedure/search/sales?section_ids%5B%5D=6&page_limit=10"
    procedure = "https://www.fabrikant.ru/v2/trades/procedure/view/PROC-A"
    detail = "https://www.fabrikant.ru/v2/trades/procedure/lot/view/PROC-A/LOT-A"

    catalogue_html = f"""
    <html><body>
      <a href="{procedure}">Процедура банкротства</a>
      <a href="/procedure/search/sales?section_ids%5B%5D=6&page_limit=10&page_number=2">2</a>
    </body></html>
    """.encode()
    procedure_html = f"""
    <html><body>
      <section><h3>Лот №1. Земельный участок - Прием заявок</h3>
      <a href="{detail}">Просмотр</a></section>
    </body></html>
    """.encode()
    detail_html = """
    <html><body>
      <h2>Лот №1. Земельный участок - Прием заявок</h2>
      <div>Предмет договора Земельный участок площадью 1000 м²,
      кадастровый номер 50:01:001:123.</div>
      <div>Категория объекта Земельные участки</div>
      <div>Начальная цена предмета договора Цена без НДС:
      900 000,00 Рубль (RUB)</div>
      <div>Дата и время окончания приема заявок 20.11.2026 10:00</div>
      <p>Публичная индивидуальная карточка лота содержит достаточное описание.</p>
    </body></html>
    """.encode()

    session = RouteSession({
        listing: FakeResponse(catalogue_html, listing),
        procedure: FakeResponse(procedure_html, procedure),
        detail: FakeResponse(detail_html, detail),
    })
    connector = SOURCE_CONNECTOR_TYPES["fabrikant.ru"](session=session)

    result = asyncio.run(connector.search({}))

    assert result.next_cursor == "2"
    assert [lot.external_id for lot in result.items] == [
        "fabrikant.ru:PROC-A:LOT-A"
    ]
    assert result.items[0].start_price == 900_000
    assert result.items[0].auction_status == "active"
    assert result.items[0].raw_data["trade_id"] == "PROC-A"
    assert result.items[0].raw_data["lot_id"] == "LOT-A"
    assert session.requested_urls == [listing, procedure, detail]


def test_fabrikant_catalogue_fails_closed_when_procedure_has_no_lot_route() -> None:
    listing = "https://www.fabrikant.ru/procedure/search/sales?section_ids%5B%5D=6&page_limit=10"
    procedure = "https://www.fabrikant.ru/v2/trades/procedure/view/PROC-ONLY"
    catalogue = f'<a href="{procedure}">Процедура</a>'.encode()
    procedure_html = b"<html><body><h2>Lot data but no canonical lot link</h2></body></html>"
    session = RouteSession({
        listing: FakeResponse(catalogue, listing),
        procedure: FakeResponse(procedure_html, procedure),
    })
    connector = SOURCE_CONNECTOR_TYPES["fabrikant.ru"](session=session)

    with pytest.raises(SourceSchemaError, match="individual lot route"):
        asyncio.run(connector.search({}))
