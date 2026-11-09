from __future__ import annotations

from bankrotai.connectors.etp_sources.common import parse_listing_html
from bankrotai.connectors.etp_sources.profiles import PROFILES
from bankrotai.connectors.etp_sources.sistematorg_contract import (
    classify_sistematorg_asset,
    parse_sistematorg_catalogue_page,
    parse_sistematorg_detail_lots,
)


PROFILE = next(p for p in PROFILES if p.source_id == "sistematorg.com")


def test_sistematorg_catalogue_keeps_trade_and_each_lot_identity() -> None:
    html = """
    <table><tr onclick="window.location='/trade_view.php?trade_nid=70001'">
      <td>20401-ОАОФ</td><td>09.11.2026</td><td>
      Всего лотов: 3.
      Лот № 1: Автомобиль LADA.
      Лот № 2: Земельный участок 76:12:0000123:456 площадью 1200 кв. м.
      Лот № 3: Право требования задолженности.
      </td></tr></table>
    """
    page = parse_sistematorg_catalogue_page(html, page=1)
    assert len(page.procedures) == 1
    procedure = page.procedures[0]
    assert procedure.trade_id == "70001"
    assert procedure.procedure_number == "20401-ОАОФ"
    assert [lot.lot_id for lot in procedure.lots] == ["1", "2", "3"]
    assert [lot.category for lot in procedure.lots] == ["vehicle", "land", "receivables"]

    imported = parse_listing_html(PROFILE, html)
    assert [lot.external_id for lot in imported] == ["sistematorg.com:70001:2"]
    assert imported[0].start_price is None
    assert imported[0].current_price is None
    assert imported[0].raw_data["trade_id"] == "70001"
    assert imported[0].raw_data["lot_id"] == "2"
    assert imported[0].raw_data["individual_lot_identity_verified"] is True
    assert imported[0].raw_data["per_lot_detail_verified"] is False


def test_sistematorg_multi_lot_detail_never_copies_first_price() -> None:
    html = """
    <html><body>
    Идентификационный номер: 20401-ОАОФ
    Тип торгов: Аукцион с открытой формой представления цены
    Дата начала представления заявок на участие: 01.11.2026 10:00 (время московское)
    Дата окончания представления заявок на участие: 20.11.2026 10:00 (время московское)
    Дата проведения: 25.11.2026 10:00 (время московское)
    Всего лотов: 2.
    Лот № 1:
      Номер лота: 1
      Наименование имущества: Автомобиль LADA VESTA
      Сведения об имуществе: VIN XTAGFK110NY607362
      Начальная цена: 800 000.00 руб.
      Размер задатка: 80 000.00 руб.
      Статус торгов: Торги объявлены
    Лот № 2:
      Номер лота: 2
      Наименование имущества: Земельный участок
      Сведения об имуществе: кадастровый номер 76:12:0000123:456, площадь 1200 кв. м,
      расположен по адресу: Ярославская область, Некрасовский район
      Начальная цена: 1 950 000.50 руб.
      Шаг аукциона: 97 500.03 руб.
      Размер задатка: 195 000.05 руб.
      Статус торгов: Прием заявок
    </body></html>
    """
    lots = parse_sistematorg_detail_lots(
        html, detail_url="https://sistematorg.com/trade_view.php?trade_nid=70001"
    )
    assert [lot.external_id for lot in lots] == [
        "sistematorg.com:70001:1", "sistematorg.com:70001:2"
    ]
    assert [lot.start_price for lot in lots] == [800_000.0, 1_950_000.5]
    assert lots[0].category == "vehicle"
    assert lots[1].category == "land"
    assert lots[1].cadastral_number == "76:12:0000123:456"
    assert lots[1].area == 1200
    assert lots[1].raw_data["price_trade_id"] == "70001"
    assert lots[1].raw_data["price_lot_id"] == "2"
    assert lots[1].raw_data["per_lot_detail_verified"] is True
    assert lots[1].application_start_at.isoformat() == "2026-11-01T10:00:00"
    assert lots[1].application_deadline.isoformat() == "2026-11-20T10:00:00"
    assert lots[1].auction_at.isoformat() == "2026-11-25T10:00:00"
    assert lots[1].auction_step_amount == 97_500.03
    assert lots[1].auction_type == "Аукцион с открытой формой представления цены"


def test_sistematorg_public_offer_periods_stay_bound_to_the_lot() -> None:
    html = """
    <html><body>
    Идентификационный номер: 19169-ОТПП
    Вид торгов: Публичное предложение
    Всего лотов: 2.
    Лот № 1:
      Номер лота: 1
      Наименование имущества: Право требования к ООО Альфа
      Сведения об имуществе: дебиторская задолженность
      Начальная цена: 100 000.00 руб.
      Размер задатка: 10 000.00 руб.
      Статус торгов: Прием заявок
      Периоды проведения торгов
      01.11.2026 10:00 05.11.2026 10:00 100 000.00 10 000.00
      05.11.2026 10:01 10.11.2026 10:00 90 000.00 9 000.00
    Лот № 2:
      Номер лота: 2
      Наименование имущества: Земельный участок
      Сведения об имуществе: кадастровый номер 50:01:0000001:22
      Начальная цена: 2 000 000.00 руб.
      Размер задатка: 200 000.00 руб.
      Статус торгов: Прием заявок
      Периоды проведения торгов
      01.11.2026 10:00 05.11.2026 10:00 2 000 000.00 200 000.00
      05.11.2026 10:01 10.11.2026 10:00 1 800 000.00 180 000.00
    </body></html>
    """
    lots = parse_sistematorg_detail_lots(
        html, detail_url="https://sistematorg.com/trade_view.php?trade_nid=63098"
    )
    second = lots[1]
    assert second.start_price == 2_000_000
    assert second.current_price is None
    assert second.public_offer_schedule == [
        {
            "application_start_at": "2026-11-01T10:00:00",
            "application_deadline": "2026-11-05T10:00:00",
            "price": "2000000.00",
            "deposit": "200000.00",
        },
        {
            "application_start_at": "2026-11-05T10:01:00",
            "application_deadline": "2026-11-10T10:00:00",
            "price": "1800000.00",
            "deposit": "180000.00",
        },
    ]
    assert second.raw_data["public_offer_schedule_verified"] is True


def test_sistematorg_issue_1065_negative_fixture_rights_are_not_property() -> None:
    # Sanitized from the first-party multi-lot shape documented for trade_nid=63098.
    values = [
        "Право требования задолженности по договору займа",
        "Дебиторская задолженность юридического лица",
        "Право требования к контрагенту",
        "Право требования денежных средств",
        "Дебиторская задолженность",
        "Право требования по исполнительному листу",
    ]
    assert [classify_sistematorg_asset(value) for value in values] == [
        "receivables"
    ] * 6


def test_sistematorg_single_lot_1166_compact_fallback_remains_strict() -> None:
    html = """
    <html><body><h1>Торги по продаже квартиры</h1>
    Всего лотов: 1. Лот № 1: квартира 50:16:0302009:4559.
    Начальная цена 4 609 800.00 руб. Размер задатка 460 980 руб.
    Статус торгов: Торги объявлены.
    Официальная карточка процедуры с подробным описанием имущества.
    </body></html>
    """
    lots = parse_sistematorg_detail_lots(
        html, detail_url="https://sistematorg.com/trade_view.php?trade_nid=36750"
    )
    assert len(lots) == 1
    assert lots[0].external_id == "sistematorg.com:36750:1"
    assert lots[0].start_price == 4_609_800
