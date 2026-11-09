from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources import PROFILES, SourceSchemaError, parse_detail_html, parse_listing_html


def _profile():
    return next(profile for profile in PROFILES if profile.source_id == "aukcioncenter.ru")


def test_active_catalogue_splits_procedure_into_individual_property_lots_and_excludes_vehicle():
    html = """
    <table>
      <tr>
        <th>№</th><th>Предмет торгов</th><th>Начало приема заявок</th><th>Завершение приема заявок</th>
      </tr>
      <tr onclick="location.href='/trade/view/purchase/general.html?id=103550001'">
        <td>7600-ОАОФ</td>
        <td>
          Лот №1: Земельный участок, площадь 1000 кв. м,
          кадастровый номер 76:01:000001:101.
          Лот №2: Автомобиль, VIN XTA00000000000001.
        </td>
        <td>01.11.2026 09:00</td><td>01.12.2026 18:00</td>
      </tr>
    </table>
    """
    lots = parse_listing_html(_profile(), html, page=1)
    assert [lot.external_id for lot in lots] == [
        "aukcioncenter.ru:103550001:1",
        "aukcioncenter.ru:103550001:2",
    ]
    land, vehicle = lots
    assert land.procedure_number == "7600-ОАОФ"
    assert land.category == "land"
    assert land.raw_data["eligible_property"] is True
    assert land.cadastral_number == "76:01:000001:101"
    assert land.area == 1000
    assert vehicle.category == "vehicle"
    assert vehicle.raw_data["eligible_property"] is False
    assert all(lot.start_price is None and lot.current_price is None for lot in lots)
    assert all(lot.auction_status == "active" for lot in lots)
    assert land.application_start_at.isoformat() == "2026-11-01T09:00:00"
    assert land.application_deadline.isoformat() == "2026-12-01T18:00:00"
    assert land.raw_data["lot_number"] == 1
    assert land.raw_data["individual_lot_identity_verified"] is True
    assert land.raw_data["listing_price_unverified"] is True


def test_single_lot_requires_source_lot_number_in_url_instead_of_inventing_lot_one():
    with_lot = """
    <table><tr>
      <td>7601-ОАОФ</td>
      <td><a href="/trade/view/purchase/general.html?id=103550002&amp;lotNumber=1">
        Квартира, кадастровый номер 77:01:000001:12, площадь 40 кв. м
      </a></td>
      <td>02.11.2026 09:00</td><td>02.12.2026 18:00</td>
    </tr></table>
    """
    lots = parse_listing_html(_profile(), with_lot)
    assert [lot.external_id for lot in lots] == ["aukcioncenter.ru:103550002:1"]

    without_lot = with_lot.replace("&amp;lotNumber=1", "")
    assert parse_listing_html(_profile(), without_lot) == []


def test_mixed_property_and_nonproperty_inside_one_lot_is_not_property_eligible():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550003')">
      <td>7602-ОАОФ</td>
      <td>Лот №1: Нежилое помещение и отдельный автомобиль как единый комплект.</td>
      <td>03.11.2026 09:00</td><td>03.12.2026 18:00</td>
    </tr></table>
    """
    lots = parse_listing_html(_profile(), html)
    assert len(lots) == 1
    assert lots[0].category == "mixed"
    assert lots[0].raw_data["eligible_property"] is False


def test_general_procedure_price_is_never_accepted_without_selected_lot_section():
    html = """
    <html><body>
      <h1>Информация о торгах</h1>
      <p>Начальная цена продажи 99 000 000,00 руб.</p>
      <p>Дата начала приема заявок 01.11.2026 09:00.</p>
      <p>Дата окончания приема заявок 01.12.2026 18:00.</p>
    </body></html>
    """
    url = "https://aukcioncenter.ru/trade/view/purchase/general.html?id=103550004&lotNumber=2"
    with pytest.raises(SourceSchemaError, match="individual_lot_section_not_proven"):
        parse_detail_html(
            _profile(), html, detail_url=url,
            external_id="aukcioncenter.ru:103550004:2",
        )


def test_detail_price_and_property_fields_are_bound_only_to_selected_lot():
    html = """
    <html><body>
      <p>Дата начала приема заявок: 01.11.2026 09:00</p>
      <p>Дата окончания приема заявок: 01.12.2026 18:00</p>
      <p>Аукцион состоится 15.12.2026 12:00</p>
      <p>идёт приём заявок</p>
      <div>
        Лот №1: Земельный участок, площадь 1200 кв. м,
        адрес: Ярославская область, район Тестовый,
        кадастровый номер 76:01:000001:101.
        Начальная цена 1 250 000,50 руб. Текущая цена 1 100 000,25 руб.
        Размер задатка 250 000 руб.
        Лот №2: Нежилое помещение, площадь 55 кв. м,
        кадастровый номер 76:01:000001:202.
        Начальная цена 8 000 000,00 руб. Текущая цена 7 500 000,00 руб.
      </div>
    </body></html>
    """
    url = "https://aukcioncenter.ru/trade/view/purchase/general.html?id=103550005&lotNumber=1"
    detail = parse_detail_html(
        _profile(), html, detail_url=url,
        external_id="aukcioncenter.ru:103550005:1",
    )
    assert detail.start_price == 1_250_000.50
    assert detail.current_price == 1_100_000.25
    assert detail.cadastral_number == "76:01:000001:101"
    assert detail.area == 1200
    assert "Ярославская область" in detail.address
    assert detail.auction_status == "active"
    assert detail.application_start_at.isoformat() == "2026-11-01T09:00:00"
    assert detail.application_deadline.isoformat() == "2026-12-01T18:00:00"
    assert detail.auction_at.isoformat() == "2026-12-15T12:00:00"
    assert detail.raw_data["detail_start_price_verified"] is True
    assert detail.raw_data["detail_current_price_verified"] is True
    assert "8 000 000" not in detail.description


def test_application_end_date_does_not_become_closed_status():
    html = """
    <html><body>
      <p>Дата окончания приема заявок: 01.01.2020 18:00</p>
      <div>Лот №1: Квартира, площадь 35 кв. м,
      кадастровый номер 77:01:000001:12. Начальная цена 2 000 000 руб.</div>
    </body></html>
    """
    url = "https://aukcioncenter.ru/trade/view/purchase/general.html?id=103550006&lotNumber=1"
    detail = parse_detail_html(
        _profile(), html, detail_url=url,
        external_id="aukcioncenter.ru:103550006:1",
    )
    assert detail.auction_status == "unknown"
    assert detail.application_deadline.isoformat() == "2020-01-01T18:00:00"


@pytest.mark.parametrize(
    ("source_text", "expected"),
    [
        ("торги завершены", "closed"),
        ("торги отменены", "closed"),
        ("идёт приём заявок", "active"),
    ],
)
def test_only_explicit_source_lifecycle_marker_sets_status(source_text, expected):
    html = f"""
    <html><body><p>{source_text}</p>
      <div>Лот №1: Квартира, площадь 35 кв. м,
      кадастровый номер 77:01:000001:12. Начальная цена 2 000 000 руб.</div>
    </body></html>
    """
    url = "https://aukcioncenter.ru/trade/view/purchase/general.html?id=103550007&lotNumber=1"
    detail = parse_detail_html(
        _profile(), html, detail_url=url,
        external_id="aukcioncenter.ru:103550007:1",
    )
    assert detail.auction_status == expected
    assert detail.raw_data["source_status_text"] == source_text
