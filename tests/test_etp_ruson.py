from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from bankrotai.connectors.etp_sources.ruson import (
    RusonReadonlyConnector,
    RusonSchemaError,
    build_next_request,
    build_page_request,
    is_terminal_page,
    parse_catalogue_page,
    parse_detail_html,
    verify_identity,
)


FIXTURES = Path(__file__).parent / "fixtures" / "etp" / "ruson"


def _parse(name: str, lot_id: str, *, observed_at_utc: datetime | None = None):
    return parse_detail_html(
        (FIXTURES / name).read_text(encoding="utf-8"),
        detail_url=f"https://rus-on.ru/bankrot/lot_view.php?lot_nid={lot_id}",
        external_id=f"rus-on.ru:{lot_id}",
        observed_at_utc=observed_at_utc,
    )


def test_ruson_ordinary_auction_uses_structured_lot_fields_not_page_boilerplate() -> None:
    lot = _parse("ordinary_active.html", "900001")
    assert lot.external_id == "rus-on.ru:900001"
    assert lot.title == "Квартира"
    assert lot.category == "real_estate"
    assert lot.cadastral_number == "78:36:0005511:8632"
    assert lot.area == 38.3
    assert lot.address == "г. Санкт-Петербург, улица Тестовая, д. 13"
    assert lot.start_price == 7_415_000
    assert lot.current_price is None
    assert lot.deposit_amount == 1_483_000
    assert lot.auction_step_amount == 370_750
    assert lot.auction_status == "scheduled"
    assert lot.auction_type == "auction"
    assert lot.application_start_at == datetime(2026, 11, 16, 10, 0)
    assert lot.application_deadline == datetime(2026, 12, 14, 10, 0)
    assert lot.auction_at == datetime(2026, 12, 21, 10, 0)
    assert lot.auction_timezone == "Europe/Moscow"
    assert lot.raw_data["ruson_initial_price_had_currency"] is False
    assert lot.raw_data["ruson_detail_template"] == "auction"


def test_ruson_public_offer_derives_current_price_only_from_active_period() -> None:
    lot = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 9, 1, 0),
    )
    assert lot.external_id == "rus-on.ru:900002"
    assert lot.auction_type == "public_offer"
    assert lot.auction_status == "active"
    assert lot.start_price == 325_802.50
    assert lot.current_price == 293_222.25
    assert lot.next_interval_price == 278_561.14
    assert lot.next_price_reduction_at == datetime(2026, 11, 11, 14, 0)
    assert lot.public_offer_schedule is not None
    assert lot.public_offer_schedule[1]["starts_at"] == "2026-11-08T14:00:00"
    assert lot.public_offer_schedule[1]["ends_at"] == "2026-11-11T14:00:00"
    assert lot.public_offer_schedule[1]["source_timezone"] == "Europe/Moscow"
    assert [row["price"] for row in lot.public_offer_schedule] == [
        325_802.50, 293_222.25, 278_561.14,
    ]
    assert lot.raw_data["ruson_current_price_source"] == "public_offer_schedule"
    assert lot.raw_data["ruson_initial_price_format"] == "decimal_dot"
    assert lot.raw_data["ruson_public_offer_currency"] == "RUB"


def test_ruson_ended_detail_accepts_currency_suffix_comma_decimal_without_inventing_current_price() -> None:
    lot = _parse(
        "public_offer_ended_currency.html",
        "900003",
        observed_at_utc=datetime(2026, 11, 9, 1, 0),
    )
    assert lot.start_price == 4_673_438.98
    assert lot.current_price is None
    assert lot.auction_status == "closed"
    assert lot.auction_type == "public_offer"
    assert lot.raw_data["ruson_initial_price_had_currency"] is True
    assert lot.raw_data["ruson_initial_price_format"] == "decimal_comma"


def test_ruson_vehicle_is_not_false_real_estate_because_page_contains_real_estate_label() -> None:
    lot = _parse("vehicle_active.html", "900004")
    assert lot.category == "vehicle"
    assert lot.vin == "XUUCD26UJC0002820"
    assert lot.start_price == 850_000
    assert lot.raw_data["ruson_source_categories"] == "Транспортные средства"


def test_ruson_public_offer_end_minute_is_inclusive_without_one_minute_price_gap() -> None:
    before_boundary = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 11, 10, 59, 59),
    )
    at_boundary = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 11, 11, 0, 0),
    )
    assert before_boundary.current_price == 293_222.25
    assert at_boundary.current_price == 278_561.14


def test_ruson_legacy_1166_case_keeps_labelled_no_rub_price_dates_and_ended_status() -> None:
    html = """
    <html><body><h1>Нежилое помещение 50:46:0060406:241</h1>
    Начальная цена 5205000.00
    Дата окончания приема заявок 19.08.2022 18:00
    Дата проведения 22.08.2022 10:00
    Торги не состоялись
    Официальная карточка содержит подробное описание продаваемого имущества.
    </body></html>
    """
    lot = parse_detail_html(
        html,
        external_id="rus-on.ru:27424",
        detail_url="https://rus-on.ru/bankrot/lot_view.php?lot_nid=27424",
    )
    assert lot.start_price == 5_205_000
    assert lot.application_deadline == datetime(2022, 8, 19, 18, 0)
    assert lot.auction_at == datetime(2022, 8, 22, 10, 0)
    assert lot.auction_status == "closed"


def test_ruson_catalogue_identity_deduplicates_same_lot_and_proves_next_page() -> None:
    html = """
    <form id="tradesearch" method="get" action="/bankrot/trade_list.php">
      <input type="hidden" name="pagenum" value="1">
      <input type="checkbox" checked name="trade_state[]" value="Прием заявок">
      <input type="checkbox" name="trade_state[]" value="Торги завершены">
      <a href="/bankrot/lot_view.php?lot_nid=115011">Лот 1</a>
      <a href="/bankrot/lot_view.php?lot_nid=115011">Земельный участок площадью 792 кв. м.</a>
      <a onclick="pagenum_send(2)">2</a>
    </form>
    """
    page = parse_catalogue_page(html, page=1)
    assert [item.external_id for item in page.items] == ["rus-on.ru:115011"]
    assert page.next_cursor == "2"
    request = build_next_request(html, page=1)
    assert request.method == "get"
    assert request.target_page == 2
    assert ("pagenum", "2") in request.fields
    assert ("trade_state[]", "Прием заявок") in request.fields
    assert ("trade_state[]", "Торги завершены") not in request.fields
    direct = build_page_request(html, target_page=128)
    assert ("pagenum", "128") in direct.fields


def test_ruson_terminal_page_and_identity_mismatch_fail_closed() -> None:
    html = """
    <form id="tradesearch" method="get" action="/bankrot/trade_list.php">
      <input type="hidden" name="pagenum" value="3">
      <a href="/bankrot/lot_view.php?lot_nid=115011">Земельный участок</a>
    </form>
    """
    page = parse_catalogue_page(html, page=3)
    assert page.next_cursor is None
    assert is_terminal_page(html, page=3) is True
    assert verify_identity(
        "rus-on.ru:115011",
        "https://rus-on.ru/bankrot/lot_view.php?lot_nid=115011",
    ) == "115011"
    with pytest.raises(RusonSchemaError, match="identity mismatch"):
        verify_identity(
            "rus-on.ru:115011",
            "https://rus-on.ru/bankrot/lot_view.php?lot_nid=115012",
        )


def test_ruson_source_connector_fetches_detail_without_shared_dispatcher(monkeypatch) -> None:
    expected = _parse("ordinary_active.html", "900001")
    calls: list[str] = []

    async def fake_fetch(self, external_id: str):
        calls.append(external_id)
        return expected

    monkeypatch.setattr(RusonReadonlyConnector, "fetch_lot", fake_fetch)
    connector = RusonReadonlyConnector()
    actual = asyncio.run(connector.fetch_lot("rus-on.ru:900001"))
    assert actual is expected
    assert calls == ["rus-on.ru:900001"]


def test_ruson_reported_pereslavl_location_uses_property_location_field() -> None:
    html = """
    <html><body><table>
      <tr><td>Наименование имущества</td><td>Земельный участок</td></tr>
      <tr><td>Сведения об имуществе (предприятии) должника, выставляемом на торги, его составе, характеристиках, описание</td>
          <td>Земельный участок, кадастровый номер 76:11:043902:114</td></tr>
      <tr><td>Местонахождение имущества</td>
          <td>Ярославская область, р-н. Переславский, Глебовский</td></tr>
      <tr><td>Начальная цена</td><td>1 000 000.00</td></tr>
      <tr><td>Статус торгов</td><td>Торги не состоялись</td></tr>
    </table>
    Дополнительные публичные сведения о порядке продажи и имуществе должника.
    </body></html>
    """
    lot = parse_detail_html(
        html,
        external_id="rus-on.ru:26414",
        detail_url="https://rus-on.ru/bankrot/lot_view.php?lot_nid=26414",
    )

    assert lot.address == "Ярославская область, р-н. Переславский, Глебовский"
    assert lot.cadastral_number == "76:11:043902:114"
    assert lot.start_price == 1_000_000
    assert lot.auction_status == "closed"


def test_ruson_unproven_detail_shell_fails_closed_instead_of_counting_as_verified() -> None:
    html = """
    <html><body>
      <h1>РОССИЯ ОнЛайн</h1>
      <nav>Торги в банкротстве Контакты Тарифы Инструкции Центр поддержки пользователей</nav>
      <p>Запрошенная карточка не содержит доказанных сведений конкретного лота.</p>
    </body></html>
    """
    with pytest.raises(RusonSchemaError, match="no proven lot-level fields"):
        parse_detail_html(
            html,
            external_id="rus-on.ru:114013",
            detail_url="https://rus-on.ru/bankrot/lot_view.php?lot_nid=114013",
        )
