"""UTPL individual-lot regression fixtures, sanitized: no personal data."""
import pytest

from bankrotai.connectors.etp_sources import PROFILES, SourceSchemaError, parse_detail_html
from bankrotai.connectors.etp_sources.utpl_contract import parse_utpl_detail_fields, UtplLotSchemaError


def profile():
    return next(p for p in PROFILES if p.source_id == "utpl.ru")


def detail(lot=1, name="Земельный участок", initial="625 560,07",
           current="500 448,07", status="Прием заявок"):
    return f"""<html><body>
       <h1>Информация о публичном предложении</h1>
       <p>Общая цена процедуры: 9 999 999,00 руб.</p>
       <div>Информация о лоте №{lot}</div>
       <table><tr><td>Номер:</td><td>{lot}</td><td>Дата начала первого интервала:</td><td>02.11.2026 10:00</td></tr>
       <tr><td>Наименование:</td><td>{name}</td><td>Дата начала представления заявок на участие:</td><td>02.11.2026 10:00</td></tr>
       <tr><td>Статус:</td><td>{status}</td><td>Дата окончания представления заявок на участие:</td><td>20.11.2026 10:00</td></tr>
       <tr><td>Дата проведения:</td><td>25.11.2026 12:00</td><td>Текущая цена, руб.:</td><td>{current}</td></tr>
       <tr><td>Начальная цена, руб.:</td><td>{initial}</td></tr></table>
       <p>Сведения об имуществе должника, его составе, характеристиках, описание, порядок ознакомления:
       Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м., адрес: г. Тестовый, ул. Полевая, 1.</p>
       <p>Классификатор ЕФРСБ: Земельные участки</p>
       <p>Порядок оформления участия в торгах: Задаток 999 000,00 руб.</p>
       <p>Другие условия продажи и порядок подачи заявления через официальную площадку.</p>
       </body></html>"""


def test_individual_lot_price_and_identity_isolated():
    result = parse_utpl_detail_fields(detail())
    assert result["lot_number"] == "1"
    assert result["initial_price"] == 625560.07
    assert result["current_price"] == 500448.07
    assert result["category"] == "land"
    assert result["property_kinds"] == ("land",)
    assert result["cadastral_number"] == "61:45:0000392:433"
    assert result["area"] == 232.7
    assert result["application_deadline"].isoformat() == "2026-11-20T10:00:00"
    assert result["auction_at"].isoformat() == "2026-11-25T12:00:00"
    lot = parse_detail_html(profile(), detail(),
        detail_url="https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/")
    assert lot.start_price == 625560.07
    assert lot.current_price == 500448.07
    assert lot.auction_status == "active"
    assert lot.cadastral_number == "61:45:0000392:433"
    assert lot.area == 232.7
    assert lot.application_deadline.isoformat() == "2026-11-20T10:00:00"
    assert lot.auction_at.isoformat() == "2026-11-25T12:00:00"
    assert lot.raw_data["utpl_property_kinds"] == ["land"]
    assert lot.raw_data["utpl_lot_section_verified"] is True


def test_claim_right_does_not_become_real_estate_from_procedure_header():
    html = detail(name="Право требования по договору на квартиру").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Уступка требований по кредитным обязательствам",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "receivables"
    assert result["property_kinds"] == ("receivables",)


def test_lease_is_not_sale_of_land():
    result = parse_utpl_detail_fields(detail(name="Право аренды земельного участка"))
    assert result["category"] == "other"
    assert "lease" in result["property_kinds"]
    assert "land" in result["property_kinds"]


def test_unrelated_price_not_reused_for_lot():
    result = parse_utpl_detail_fields(detail(initial="не указана", current="не указана"))
    assert result["initial_price"] is None
    assert result["current_price"] is None


def test_multi_lot_detail_fails_closed():
    html = detail() + "<div>Информация о лоте №2</div><p>Наименование: Иной лот</p>"
    with pytest.raises(UtplLotSchemaError, match="exactly one"):
        parse_utpl_detail_fields(html)


def test_wrong_external_identity_fails_closed():
    with pytest.raises(SourceSchemaError, match="requested lot ID"):
        parse_detail_html(profile(), detail(),
            detail_url="https://bankrupt.utpl.ru/public/auctions/lots/view/99/",
            external_id="utpl.ru:auctions-98")


@pytest.mark.parametrize(("label", "expected"), [
    ("Окончен", "closed"),
    ("Не состоялся", "closed"),
    ("Извещение опубликовано", "scheduled"),
    ("Прием заявок", "active"),
    ("Приостановлен", "paused"),
    ("Отменён организатором", "closed"),
    ("Неизвестный статус", "unknown"),
])
def test_official_utpl_status_labels(label, expected):
    from bankrotai.connectors.etp_sources.utpl_contract import normalize_utpl_status
    assert normalize_utpl_status(label) == expected
    parsed = parse_detail_html(
        profile(), detail(status=label),
        detail_url="https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/",
    )
    assert parsed.auction_status == expected


def test_structured_classifier_preserves_mixed_land_and_real_estate():
    html = detail(name="Комплекс недвижимого имущества").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Жилые здания (помещения) Земельные участки",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "real_estate"
    assert result["property_kinds"] == ("land", "real_estate")


def test_equipment_classifier_is_not_misreported_as_vehicle():
    html = detail(name="Комплект технологического оборудования").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Машины и оборудование прочие, не включенные в другие группировки",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "other"
    assert result["property_kinds"] == ("equipment",)


def test_title_stops_before_lot_dates_from_live_layout():
    parsed = parse_utpl_detail_fields(detail(name="Имущество ООО Тест"))
    assert parsed["title"] == "Имущество ООО Тест"
    assert parsed["source_status_text"] == "Прием заявок"
