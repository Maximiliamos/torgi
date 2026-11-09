"""UTPL source-contract regression fixtures; no shared dispatcher dependency."""
from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources import (
    PROFILES,
    SOURCE_CONNECTOR_TYPES,
    parse_detail_html,
    parse_listing_html,
)
from bankrotai.connectors.etp_sources.pagination import has_next_page
from bankrotai.connectors.etp_sources.utpl_contract import (
    PRODUCTION_ENABLED,
    SAFE_FOR_RECONCILIATION,
    UtplLotSchemaError,
    is_terminal_page,
    normalize_utpl_status,
    parse_utpl_catalogue_page,
    parse_utpl_detail,
    parse_utpl_detail_fields,
    utpl_next_postback,
    validate_sequential_page_state,
    verify_detail_identity,
)


def detail(
    lot: int = 1,
    name: str = "Земельный участок",
    initial: str = "625 560,07",
    current: str = "500 448,07",
    status: str = "Прием заявок",
) -> str:
    return f"""<html><body>
      <h1>Информация о публичном предложении №0009410</h1>
      <p>Общая цена процедуры: 9 999 999,00 руб.</p>
      <div>Информация о лоте №{lot}</div>
      <table>
        <tr><td>Номер:</td><td>{lot}</td><td>Дата начала первого интервала:</td><td>02.11.2026 10:00</td></tr>
        <tr><td>Наименование:</td><td>{name}</td><td>Дата начала представления заявок на участие:</td><td>02.11.2026 10:00</td></tr>
        <tr><td>Статус:</td><td>{status}</td><td>Дата окончания представления заявок на участие:</td><td>20.11.2026 10:00</td></tr>
        <tr><td>Дата проведения:</td><td>25.11.2026 12:00</td><td>Текущая цена, руб.:</td><td>{current}</td></tr>
        <tr><td>Начальная цена, руб.:</td><td>{initial}</td></tr>
      </table>
      <p>Сведения об имуществе должника, его составе, характеристиках, описание, порядок ознакомления:
      Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м.,
      адрес: г. Тестовый, ул. Полевая, 1.</p>
      <p>Классификатор ЕФРСБ: Земельные участки</p>
      <p>Порядок оформления участия в торгах: Задаток 999 000,00 руб.</p>
      <p>Другие условия продажи и порядок подачи заявления через официальную площадку.</p>
    </body></html>"""


def test_individual_lot_price_identity_and_dates_are_isolated() -> None:
    parsed = parse_utpl_detail(
        "https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/",
        detail(),
    )
    assert parsed["external_id"] == "utpl.ru:public-offers-127814"
    assert parsed["lot_number"] == "1"
    assert parsed["procedure_number"] == "0009410"
    assert parsed["initial_price"] == 625560.07
    assert parsed["current_price"] == 500448.07
    assert parsed["status"] == "active"
    assert parsed["category"] == "land"
    assert parsed["property_kinds"] == ("land",)
    assert parsed["cadastral_number"] == "61:45:0000392:433"
    assert parsed["area"] == 232.7
    assert parsed["application_deadline"].isoformat() == "2026-11-20T10:00:00"
    assert parsed["auction_at"].isoformat() == "2026-11-25T12:00:00"


def test_claim_right_does_not_become_real_estate_from_procedure_header() -> None:
    html = detail(name="Право требования по договору на квартиру").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Уступка требований по кредитным обязательствам",
    ).replace(
        "Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м.,\n      адрес: г. Тестовый, ул. Полевая, 1.",
        "Право требования задолженности по кредитному договору.",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "receivables"
    assert result["property_kinds"] == ("receivables",)


def test_lease_is_not_sale_of_land() -> None:
    result = parse_utpl_detail_fields(detail(name="Право аренды земельного участка"))
    assert result["category"] == "other"
    assert "lease" in result["property_kinds"]
    assert "land" in result["property_kinds"]


def test_unrelated_procedure_or_deposit_amount_is_not_reused_as_lot_price() -> None:
    result = parse_utpl_detail_fields(detail(initial="не указана", current="не указана"))
    assert result["initial_price"] is None
    assert result["current_price"] is None


def test_multi_lot_detail_document_fails_closed() -> None:
    html = detail() + "<div>Информация о лоте №2</div><p>Наименование: Иной лот</p>"
    with pytest.raises(UtplLotSchemaError, match="exactly one"):
        parse_utpl_detail_fields(html)


def test_wrong_external_identity_fails_closed() -> None:
    with pytest.raises(UtplLotSchemaError, match="does not match"):
        verify_detail_identity(
            "https://bankrupt.utpl.ru/public/auctions/lots/view/99/",
            "utpl.ru:auctions-98",
        )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Окончен", "closed"),
        ("Не состоялся", "closed"),
        ("Извещение опубликовано", "scheduled"),
        ("Прием заявок", "active"),
        ("Приостановлен", "paused"),
        ("Отменён организатором", "closed"),
        ("Неизвестный статус", "unknown"),
    ],
)
def test_official_utpl_status_labels(label: str, expected: str) -> None:
    assert normalize_utpl_status(label) == expected


def test_structured_classifier_preserves_mixed_land_and_real_estate() -> None:
    html = detail(name="Комплекс недвижимого имущества").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Жилые здания (помещения) Земельные участки",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "real_estate"
    assert result["property_kinds"] == ("land", "real_estate")


def test_equipment_classifier_is_not_misreported_as_vehicle() -> None:
    html = detail(name="Комплект технологического оборудования").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Машины и оборудование прочие, не включенные в другие группировки",
    ).replace(
        "Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м.,\n      адрес: г. Тестовый, ул. Полевая, 1.",
        "Технологическое оборудование, станок и компрессор.",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "other"
    assert result["property_kinds"] == ("equipment",)


def test_title_stops_before_lot_dates_from_live_layout() -> None:
    parsed = parse_utpl_detail_fields(detail(name="Нежилое помещение площадью 40 кв. м."))
    assert parsed["title"] == "Нежилое помещение площадью 40 кв. м."


def test_utpl_pager_uses_exact_numeric_postback_before_forward_control() -> None:
    html = """
    <a href="javascript:__doPostBack('grid$page2','')">2</a>
    <a href="javascript:__doPostBack('grid$forward','')">&gt;&gt;</a>
    """
    assert utpl_next_postback(html, 1) == ("grid$page2", "")


def test_utpl_pager_crosses_ten_page_block_with_provider_forward_control() -> None:
    html = """
    <span>10</span>
    <a href="javascript:__doPostBack('grid$forward','')">&gt;&gt;</a>
    """
    assert utpl_next_postback(html, 10) == ("grid$forward", "")
    assert is_terminal_page(html, 10) is False


def test_utpl_pager_terminal_page_has_no_synthesized_next_request() -> None:
    html = "<a href=\"javascript:__doPostBack('grid$page9','')\">9</a><span>10</span>"
    assert utpl_next_postback(html, 10) is None
    assert is_terminal_page(html, 10) is True


def test_source_contract_never_enables_production_or_reconciliation() -> None:
    assert PRODUCTION_ENABLED is False
    assert SAFE_FOR_RECONCILIATION is False


def test_rights_from_lot_description_stay_receivables_without_classifier() -> None:
    html = detail(name="Имущество общества").replace(
        "Классификатор ЕФРСБ: Земельные участки", ""
    ).replace(
        "Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м.,\n      адрес: г. Тестовый, ул. Полевая, 1.",
        "Право требования задолженности по договору.",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "receivables"
    assert result["property_kinds"] == ("receivables",)


def test_location_label_is_accepted_as_lot_scoped_address() -> None:
    html = detail().replace(
        "адрес: г. Тестовый, ул. Полевая, 1.",
        "местоположение: г. Тестовый, ул. Полевая, 1.",
    )
    result = parse_utpl_detail_fields(html)
    assert result["address"] == "г. Тестовый, ул. Полевая, 1."


def test_mixed_lot_augments_classifier_from_official_asset_description() -> None:
    html = detail(name="Имущественный комплекс").replace(
        "Классификатор ЕФРСБ: Земельные участки",
        "Классификатор ЕФРСБ: Здания (кроме жилых) и сооружения",
    ).replace(
        "Земельный участок, кадастровый номер 61:45:0000392:433, площадь 232,7 кв. м.,\n      адрес: г. Тестовый, ул. Полевая, 1.",
        "Земельный участок кадастровый номер 23:43:0414003:38; "
        "Здание площадью 37,4 кв. м.; Право аренды земельного участка 23:43:0414003:54; "
        "Топливораздаточная колонка и компрессор.",
    )
    result = parse_utpl_detail_fields(html)
    assert result["category"] == "real_estate"
    assert result["property_kinds"] == ("lease", "land", "real_estate", "equipment")


def test_utpl_rejects_nonsequential_postback_state_jump() -> None:
    with pytest.raises(UtplLotSchemaError, match="sequential provider state"):
        validate_sequential_page_state(requested_page=12, last_page=1)


def test_catalogue_parser_keeps_identity_but_does_not_claim_property_classification() -> None:
    html = """
    <table>
      <tr><th>Номер процедуры</th><th>№ лота</th><th>Лот</th><th>Начальная цена</th><th>Текущая цена</th><th>Статус</th></tr>
      <tr><td>0009410</td><td>2</td><td><a href="/public/public-offers/lots/view/128999/">Земельный участок 50:05:0010203:1234</a></td><td>2 500 000,00</td><td>2 000 000,00</td><td>Прием заявок</td></tr>
    </table>
    """
    lots = parse_utpl_catalogue_page(html, page=1)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["external_id"] == "utpl.ru:public-offers-128999"
    assert lot["initial_price"] == 2_500_000
    assert lot["current_price"] == 2_000_000
    assert lot["property_classification_verified"] is False


def test_detail_promotes_only_structured_property_classification() -> None:
    parsed = parse_utpl_detail(
        "https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/",
        detail(),
    )
    assert parsed["category"] == "land"
    assert parsed["structured_classifier_verified"] is True
    assert parsed["property_classification_verified"] is True


def operational_profile():
    return next(item for item in PROFILES if item.source_id == "utpl.ru")


def catalogue_row_fixture() -> str:
    return """
    <table>
      <tr>
        <th>Номер процедуры</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена</th><th>Текущая цена</th><th>Статус</th>
      </tr>
      <tr>
        <td>0009410</td><td>2</td>
        <td><a href="/public/public-offers/lots/view/128999/">
          Земельный участок 50:05:0010203:1234
        </a></td>
        <td>2 500 000,00</td><td>2 000 000,00</td><td>Прием заявок</td>
      </tr>
    </table>
    """


def test_operational_listing_routes_through_utpl_contract() -> None:
    lot = parse_listing_html(operational_profile(), catalogue_row_fixture(), page=7)[0]
    assert lot.external_id == "utpl.ru:public-offers-128999"
    assert lot.start_price == 2_500_000
    assert lot.current_price == 2_000_000
    assert lot.auction_status == "active"
    assert lot.category == "other"
    assert lot.procedure_number == "9410"
    assert lot.raw_data["utpl_lot_number"] == "2"
    assert lot.raw_data["utpl_property_classification_verified"] is False


def test_operational_detail_routes_through_isolated_lot_section() -> None:
    lot = parse_detail_html(
        operational_profile(),
        detail(),
        detail_url="https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/",
        external_id="utpl.ru:public-offers-127814",
    )
    assert lot.external_id == "utpl.ru:public-offers-127814"
    assert lot.start_price == 625560.07
    assert lot.current_price == 500448.07
    assert lot.auction_status == "active"
    assert lot.category == "land"
    assert lot.cadastral_number == "61:45:0000392:433"
    assert lot.area == 232.7
    assert lot.procedure_number == "0009410"
    assert lot.raw_data["utpl_lot_number"] == "1"
    assert lot.raw_data["utpl_property_kinds"] == ["land"]
    assert lot.raw_data["utpl_property_classification_verified"] is True


def test_operational_pagination_uses_utpl_forward_control_and_terminal() -> None:
    profile = operational_profile()
    page_ten = """
      <span>10</span>
      <a href="javascript:__doPostBack('grid$forward','')">&gt;&gt;</a>
    """
    terminal = "<span>517</span>"
    assert has_next_page(profile, page_ten, 10) is True
    assert has_next_page(profile, terminal, 517) is False


def test_operational_connector_exposes_detail_enrichment_without_reconciliation() -> None:
    connector = SOURCE_CONNECTOR_TYPES["utpl.ru"]()
    assert "detail_enrichment" in connector.capabilities
    assert connector.safe_for_reconciliation is False
