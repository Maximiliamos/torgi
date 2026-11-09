from pathlib import Path

import pytest

from bankrotai.connectors.etp_sources.centerr_contract import (
    CenterRContractError,
    page_fingerprint,
    parse_catalogue_lots,
    parse_detail,
    parse_detail_facts,
    parse_listing,
    parse_next_page_postback,
    summarize_catalogue_identities,
    verify_identity,
)


FIXTURE = Path(__file__).with_name("fixtures") / "centerr_duplicate_wrong_id.html"


def test_centerr_regression_rejects_one_provider_lot_id_for_two_catalogue_cards():
    with pytest.raises(CenterRContractError, match="provider lot collision"):
        parse_catalogue_lots(FIXTURE.read_text(encoding="utf-8"))


def test_centerr_sidebar_lot_shaped_link_is_not_a_catalogue_identity():
    html = """
    <div class='news-body'>
      <a href='/public/auctions/lots/view/777777/'>Квартира из новости</a>
    </div>
    <table><tr><th>x</th></tr></table>
    """
    assert parse_catalogue_lots(html) == []


def test_centerr_catalogue_identity_uses_provider_lot_url_not_trade_id():
    html = """
    <table>
    <tr><td>0101489</td><td>Торг</td><td>3</td>
    <td><a href='/public/auctions/lots/view/1171636/'>
    Жилое помещение (квартира), площадью 51,6 кв.м., кадастровый номер 77:17:0120316:21039
    </a></td><td>15 232 320,00</td><td>Организатор</td>
    <td>09.12.2026</td><td>11.12.2026</td><td>Прием заявок</td></tr>
    </table>
    """
    item = parse_catalogue_lots(html)[0]
    assert item.external_id == "centerr.ru:auctions-1171636"
    assert item.provider_lot_id == "1171636"
    assert item.trade_id == "0101489"
    assert item.lot_number == "3"
    assert item.cadastral_number == "77:17:0120316:21039"
    assert item.area == 51.6

    normalized = parse_listing(html, page=2)[0]
    assert normalized.external_id == item.external_id
    assert normalized.raw_data["trade_id"] == "0101489"
    assert normalized.raw_data["lot_number"] == "3"
    assert normalized.raw_data["provider_lot_id"] == "1171636"
    assert normalized.raw_data["individual_lot_identity_verified"] is True


def test_centerr_detail_isolated_lot_fields_and_identity():
    html = """
    <html><body>
    <h1>Информация о лоте</h1>
    Информация об аукционе №0101489
    Наименование: Реализация имущества должника
    Информация о лоте №3
    Номер: 3 Дата проведения: 11.12.2026 14:00
    Наименование: Жилое помещение (квартира), площадью 51,6 кв.м., по адресу:
    г. Москва, ул. Тестовая, дом 6, квартира 690, кадастровый номер 77:17:0120316:21039.
    Дата начала представления заявок на участие: 03.11.2026 11:00
    Статус: Прием заявок Дата окончания представления заявок на участие: 09.12.2026 15:00
    Начальная цена, руб.: 15 232 320,00
    Классификатор ЕФРСБ: Жилые здания (помещения)
    Обеспечение заявки
    Размер обеспечения, руб.: 1 523 232,00
    </body></html>
    """
    url = "https://bankrupt.centerr.ru/public/auctions/lots/view/1171636/"
    facts = parse_detail_facts(html, detail_url=url)
    assert facts.external_id == "centerr.ru:auctions-1171636"
    assert facts.trade_id == "0101489"
    assert facts.lot_number == "3"
    assert facts.start_price == 15232320.0
    assert facts.cadastral_number == "77:17:0120316:21039"
    assert facts.area == 51.6
    assert facts.address == "г. Москва, ул. Тестовая, дом 6, квартира 690"
    assert facts.efrsb_classifier == "Жилые здания (помещения)"
    assert facts.category == "real_estate"
    assert facts.application_start_at.isoformat() == "2026-11-03T11:00:00"
    assert facts.application_deadline.isoformat() == "2026-12-09T15:00:00"
    assert facts.auction_at.isoformat() == "2026-12-11T14:00:00"

    lot = parse_detail(html, detail_url=url, external_id=facts.external_id)
    assert lot.start_price == 15232320.0
    assert lot.current_price is None
    assert lot.auction_status == "active"
    assert lot.raw_data["detail_identity_verified"] is True
    assert lot.raw_data["efrsb_classifier"] == "Жилые здания (помещения)"
    assert lot.property_type == "Жилые здания (помещения)"
    assert lot.application_deadline.isoformat() == "2026-12-09T15:00:00"


def test_centerr_detail_rejects_wrong_external_id_substitution():
    html = """
    <html><body>Информация об аукционе №0101489
    Информация о лоте №1 Номер: 1 Дата проведения: 11.12.2026 14:00
    Наименование: Земельный участок 73:10:030903:271 площадью 3373 кв. м
    Дата начала представления заявок на участие: 05.11.2026 08:00
    Статус: Прием заявок Дата окончания представления заявок: 09.12.2026 18:00
    Начальная цена, руб.: 160 000,00
    </body></html>
    """
    with pytest.raises(CenterRContractError, match="detail substitution"):
        parse_detail(
            html,
            detail_url="https://bankrupt.centerr.ru/public/auctions/lots/view/1171674/",
            external_id="centerr.ru:auctions-999999",
        )


def _catalogue_row(*, trade: str, lot: str, provider_id: str, title: str) -> str:
    return f"""
    <table><tr><td>{trade}</td><td>Торг</td><td>{lot}</td>
    <td><a href='/public/auctions/lots/view/{provider_id}/'>{title}</a></td>
    <td>1 000 000,00</td><td>Организатор</td>
    <td>09.12.2026</td><td>11.12.2026</td><td>Прием заявок</td></tr></table>
    """


def test_centerr_pagination_postback_and_terminal_detection_are_source_local():
    html = b"""
    <input type='hidden' name='__VIEWSTATE' value='state'>
    <input type='hidden' name='__EVENTVALIDATION' value='valid'>
    <a href="javascript:__doPostBack('ctl00$grid','Page$2')">2</a>
    """
    postback = parse_next_page_postback(html, next_page=2)
    assert postback is not None
    target, argument, hidden = postback
    assert target == "ctl00$grid"
    assert argument == "Page$2"
    assert hidden["__VIEWSTATE"] == "state"
    assert hidden["__EVENTVALIDATION"] == "valid"
    assert parse_next_page_postback(html, next_page=3) is None


def test_centerr_cross_page_identity_summary_detects_collision_and_fingerprints():
    first = parse_catalogue_lots(_catalogue_row(
        trade="0101001", lot="1", provider_id="900001",
        title="Квартира 50:01:0000001:1",
    ))[0]
    second = parse_catalogue_lots(_catalogue_row(
        trade="0101002", lot="2", provider_id="900001",
        title="Квартира 50:01:0000001:2",
    ))[0]
    summary = summarize_catalogue_identities([first, second])
    assert summary["unique_count"] == 1
    assert summary["duplicate_same_identity_count"] == 1
    assert summary["collisions"] == ["centerr.ru:auctions-900001"]
    assert page_fingerprint(b"page-1") == page_fingerprint(b"page-1")
    assert page_fingerprint(b"page-1") != page_fingerprint(b"page-2")


def test_centerr_verify_identity_rejects_trade_or_lot_substitution():
    catalogue = parse_catalogue_lots(_catalogue_row(
        trade="0101489", lot="3", provider_id="1171636",
        title="Квартира 77:17:0120316:21039",
    ))[0]
    detail_html = """
    <html><body>
    Информация об аукционе №0101490
    Информация о лоте №3
    Наименование: Квартира 77:17:0120316:21039
    Дата проведения: 11.12.2026 14:00
    Статус: Прием заявок
    Начальная цена, руб.: 1 000 000,00
    </body></html>
    """
    detail = parse_detail_facts(
        detail_html,
        detail_url="https://bankrupt.centerr.ru/public/auctions/lots/view/1171636/",
    )
    with pytest.raises(CenterRContractError, match="catalogue/detail identity mismatch"):
        verify_identity(catalogue, detail)
