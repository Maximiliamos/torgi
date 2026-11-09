from __future__ import annotations

import asyncio
import json

import pytest
import requests

from bankrotai.connectors.etp_sources.arbbitlot import (
    ArbbitlotConnector,
    ArbbitlotContractError,
    build_next_request,
    has_next_page,
    merge_verified_detail,
    parse_catalogue_page,
    parse_detail,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        url: str = "https://torgi.arbbitlot.ru/public/purchases-all/",
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.content)


class FakeSession:
    def __init__(self, response: FakeResponse | Exception | list[FakeResponse]) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []
        self.posts: list[tuple[str, dict[str, str]]] = []

    def _next(self):
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response

    def get(self, url, *_args, **_kwargs):
        self.requested_urls.append(url)
        response = self._next()
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, *, data=None, json=None, **_kwargs):
        self.posts.append((url, data if data is not None else json))
        response = self._next()
        if isinstance(response, Exception):
            raise response
        return response


def test_detail_parses_explicit_rub_fields_without_cross_field_leakage() -> None:
    html = """
    <html><body>
      <h1>Доля 1/5 в квартире</h1>
      Информация об аукционе №0037314
      Информация о лоте №1
      Наименование: Доля 1/5 в квартире.
      Адрес (местонахождение): Ярославская область, г. Рыбинск, ул. Тестовая, д. 1
      Кадастровый номер: 76:20:010101:123
      Площадь: 52,4 кв. м
      Статус: Извещение опубликовано
      Начальная цена, руб.: 452 906,10
      Размер задатка, руб.: 45 290,61
      Шаг, % от начальной цены: 5,00
      Дата проведения: 20.12.2026 10:00
      Дополнительные сведения о порядке участия и имуществе.
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/44914/",
    )
    assert lot.external_id == "arbbitlot.ru:auctions-44914"
    assert lot.start_price == 452906.10
    assert lot.current_price is None
    assert lot.deposit_amount == 45290.61
    assert lot.auction_step_amount is None
    assert lot.auction_step_percent == 5.0
    assert lot.procedure_number == "0037314"
    assert lot.raw_data["lot_number"] == "1"
    assert lot.raw_data["per_lot_detail_verified"] is True
    assert lot.raw_data["price_unverified"] is False
    assert lot.cadastral_number == "76:20:010101:123"
    assert lot.area == 52.4
    assert lot.auction_status == "scheduled"


def test_public_offer_keeps_initial_current_deposit_and_step_distinct() -> None:
    html = """
    <html><body>
      <h1>Земельный участок</h1>
      Информация о публичном предложении №0037071
      Информация о лоте №1
      Наименование: Земельный участок, кадастровый номер 76:01:000000:123.
      Статус: Прием заявок
      Начальная цена, руб.: 540 000,00
      Текущая цена, руб.: 486 000,00
      Размер обеспечения, руб.: 48 600,00
      Шаг, руб.: 27 000,00
      Шаг, % от начальной цены: 5,00
      Окончание приема заявок: 18.12.2026 12:00
      Подробные сведения об имуществе и порядке публичного предложения.
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://torgi.arbbitlot.ru/public/public-offers/lots/view/50001/",
    )
    assert lot.start_price == 540000
    assert lot.current_price == 486000
    assert lot.deposit_amount == 48600
    assert lot.auction_step_amount == 27000
    assert lot.auction_step_percent == 5
    assert lot.auction_status == "active"
    assert lot.raw_data["current_price_unverified"] is False


def test_missing_initial_price_fails_closed_instead_of_using_deposit_or_step() -> None:
    html = """
    <html><body>
      <h1>Земельный участок</h1>
      Информация об аукционе №0037314 Информация о лоте №2
      Статус: Прием заявок
      Размер задатка, руб.: 90 000,00
      Шаг, руб.: 45 000,00
      Шаг, % от начальной цены: 5,00
      Кадастровый номер: 76:01:000000:124
      Подробные сведения об имуществе и порядке участия в торгах.
    </body></html>
    """
    with pytest.raises(
        ArbbitlotContractError,
        match="individual detail does not prove an explicit start price",
    ):
        parse_detail(
            html,
            detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/50002/",
        )


def test_multi_lot_catalogue_keeps_per_lot_ids_prices_and_displayed_identity() -> None:
    html = """
    <table>
      <tr>
        <th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена</th><th>Статус</th>
      </tr>
      <tr>
        <td>0032715</td><td>Аукцион</td><td>1</td>
        <td><a href="/public/auctions/lots/view/41001/">Автомобиль LADA</a></td>
        <td>450 000,00</td><td>Прием заявок</td>
      </tr>
      <tr>
        <td>0032715</td><td>Аукцион</td><td>2</td>
        <td><a href="/public/auctions/lots/view/41002/">Гараж 76:01:000000:200</a></td>
        <td>900 000,00</td><td>Прием заявок</td>
      </tr>
      <tr>
        <td>0032715</td><td>Аукцион</td><td>3</td>
        <td><a href="/public/auctions/lots/view/41003/">Склад 76:01:000000:201</a></td>
        <td>1 500 000,00</td><td>Прием заявок</td>
      </tr>
    </table>
    """
    lots = parse_catalogue_page(html)
    assert [lot.external_id for lot in lots] == [
        "arbbitlot.ru:auctions-41001",
        "arbbitlot.ru:auctions-41002",
        "arbbitlot.ru:auctions-41003",
    ]
    assert len({lot.external_id for lot in lots}) == 3
    assert [lot.start_price for lot in lots] == [450000, 900000, 1500000]
    assert [lot.procedure_number for lot in lots] == ["0032715"] * 3
    assert [lot.raw_data["lot_number"] for lot in lots] == ["1", "2", "3"]


def test_public_offer_catalogue_keeps_per_lot_start_and_current_price() -> None:
    html = """
    <table>
      <tr>
        <th>№ Публичное предложение</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена, руб.</th><th>Текущая цена, руб.</th><th>Статус</th>
      </tr>
      <tr>
        <td>0037071</td><td>1</td>
        <td><a href="/public/public-offers/lots/view/40663/">
          Земельный участок 01:06:2800003:164
        </a></td>
        <td>242 100,00</td><td>169 470,00</td><td>Прием заявок</td>
      </tr>
    </table>
    """
    lot = parse_catalogue_page(html)[0]
    assert lot.external_id == "arbbitlot.ru:public-offers-40663"
    assert lot.start_price == 242100
    assert lot.current_price == 169470
    assert lot.raw_data["procedure_id"] == "0037071"
    assert lot.raw_data["lot_number"] == "1"


def test_verified_merge_preserves_catalogue_current_when_detail_has_no_current_label() -> None:
    listing_html = """
    <table>
      <tr>
        <th>№ Публичное предложение</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена, руб.</th><th>Текущая цена, руб.</th><th>Статус</th>
      </tr>
      <tr>
        <td>0037071</td><td>1</td>
        <td><a href="/public/public-offers/lots/view/40663/">
          Земельный участок 01:06:2800003:164
        </a></td>
        <td>242 100,00</td><td>169 470,00</td><td>Прием заявок</td>
      </tr>
    </table>
    """
    detail_html = """
    <html><body>
      <h1>Земельный участок 01:06:2800003:164</h1>
      Информация о публичном предложении №0037071
      Информация о лоте №1
      Статус: Прием заявок
      Начальная цена, руб.: 242 100,00
      Интервалы снижения цены:
      01.11.2026 10:00 05.11.2026 10:00 24 210,00 193 680,00
      05.11.2026 10:00 10.11.2026 10:00 24 210,00 169 470,00
      Размер обеспечения, руб.: 24 210,00
      Подробные сведения об имуществе и порядке публичного предложения.
    </body></html>
    """
    listing = parse_catalogue_page(listing_html)[0]
    detail = parse_detail(
        detail_html,
        detail_url="https://torgi.arbbitlot.ru/public/public-offers/lots/view/40663/",
    )
    enriched = merge_verified_detail(listing, detail)
    assert enriched.start_price == 242100
    assert enriched.current_price == 169470
    assert enriched.raw_data["detail_start_price_verified"] is True
    assert enriched.raw_data["detail_current_price_verified"] is False
    assert enriched.raw_data["listing_current_price_used_as_fallback"] is True


def test_aspnet_page_two_posts_state_and_reaches_terminal_page() -> None:
    first = """
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='arb-state'>
      <a href="javascript:__doPostBack('pager$page2','')">2</a>
    </form>
    <table>
      <tr><th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0032715</td><td>Аукцион</td><td>1</td>
      <td><a href='/public/auctions/lots/view/41000/'>Участок 76:01:000000:199</a></td>
      <td>800 000,00</td><td>Прием заявок</td></tr>
    </table>
    """.encode("utf-8")
    second = """
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='arb-state-2'>
    </form>
    <table>
      <tr><th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0032716</td><td>Аукцион</td><td>1</td>
      <td><a href='/public/auctions/lots/view/41002/'>Гараж 76:01:000000:200</a></td>
      <td>900 000,00</td><td>Прием заявок</td></tr>
      <tr><td>0032717</td><td>Аукцион</td><td>1</td>
      <td><a href='/public/auctions/lots/view/41003/'>Склад 76:01:000000:201</a></td>
      <td>1 500 000,00</td><td>Прием заявок</td></tr>
    </table>
    """.encode("utf-8")
    session = FakeSession([
        FakeResponse(200, first),
        FakeResponse(200, second),
    ])
    connector = ArbbitlotConnector(session=session)
    page1 = asyncio.run(connector.search({}))
    assert page1.next_cursor == "2"
    page2 = asyncio.run(connector.search({}, cursor="2"))
    assert page2.next_cursor is None
    assert [lot.external_id for lot in page2.items] == [
        "arbbitlot.ru:auctions-41002",
        "arbbitlot.ru:auctions-41003",
    ]
    assert session.posts[0][1]["__VIEWSTATE"] == "arb-state"
    assert session.posts[0][1]["__EVENTTARGET"] == "pager$page2"


def test_double_arrow_advances_beyond_visible_page_group() -> None:
    page_ten = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state-page-10'>
      <a href="javascript:__doPostBack('pager$first','')">1</a>
      <span>10</span>
      <a href="javascript:__doPostBack('pager$nextGroup','')">&gt;&gt;</a>
    </form>
    """
    assert has_next_page(page_ten, 10) is True
    action, data = build_next_request(
        page_ten,
        current_url="https://torgi.arbbitlot.ru/public/purchases-all/",
        requested_page=11,
    )
    assert action == "https://torgi.arbbitlot.ru/public/purchases-all/"
    assert data["__VIEWSTATE"] == "state-page-10"
    assert data["__EVENTTARGET"] == "pager$nextGroup"


def test_group_arrow_is_not_used_for_missing_normal_next_page() -> None:
    page_three = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state-page-3'>
      <span>3</span>
      <a href="javascript:__doPostBack('pager$nextGroup','')">&gt;&gt;</a>
    </form>
    """
    with pytest.raises(ArbbitlotContractError, match="page 4 postback target disappeared"):
        build_next_request(
            page_three,
            current_url="https://torgi.arbbitlot.ru/public/purchases-all/",
            requested_page=4,
        )


def test_organizer_cancelled_status_is_source_specific_closed() -> None:
    listing = """
    <table>
      <tr><th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0037281</td><td>Аукцион</td><td>1</td>
      <td><a href="/public/auctions/lots/view/51012/">Автомобиль VIN XTA219110GY223704</a></td>
      <td>2 300 000,00</td><td>Отменён организатором</td></tr>
    </table>
    """
    assert parse_catalogue_page(listing)[0].auction_status == "closed"

    detail = """
    <html><body>
      <h1>Автомобиль VIN XTA219110GY223704</h1>
      Информация об аукционе №0037281
      Информация о лоте №1
      Статус: Отменён организатором
      Начальная цена, руб.: 2 300 000,00
      Подробные сведения об имуществе должника и порядке проведения торгов.
    </body></html>
    """
    lot = parse_detail(
        detail,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/51012/",
    )
    assert lot.auction_status == "closed"


@pytest.mark.parametrize(
    ("detail_procedure", "detail_lot_number", "expected_error"),
    [
        ("0039999", "2", "arbbitlot_catalogue_detail_procedure_id_mismatch"),
        ("0032715", "9", "arbbitlot_catalogue_detail_lot_number_mismatch"),
    ],
)
def test_merge_rejects_catalogue_detail_identity_mismatch(
    detail_procedure: str,
    detail_lot_number: str,
    expected_error: str,
) -> None:
    listing_html = """
    <table>
      <tr><th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0032715</td><td>Аукцион</td><td>2</td>
      <td><a href="/public/auctions/lots/view/41002/">Гараж 76:01:000000:200</a></td>
      <td>900 000,00</td><td>Прием заявок</td></tr>
    </table>
    """
    listing = parse_catalogue_page(listing_html)[0]
    detail_html = f"""
    <html><body>
      <h1>Гараж 76:01:000000:200</h1>
      Информация об аукционе №{detail_procedure}
      Информация о лоте №{detail_lot_number}
      Статус: Прием заявок
      Начальная цена, руб.: 900 000,00
      Подробные сведения об имуществе и порядке проведения торгов.
    </body></html>
    """
    detail = parse_detail(
        detail_html,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/41002/",
    )
    with pytest.raises(ArbbitlotContractError, match=expected_error):
        merge_verified_detail(listing, detail)


@pytest.mark.parametrize(
    ("classifier", "expected_category"),
    [
        ("Земельные участки", "land"),
        ("Автомобили", "vehicle"),
        ("Права требования на краткосрочные долговые обязательства (дебиторская задолженность)", "receivables"),
        ("Измерительное оборудование, инструмент и комплектующие", "other"),
    ],
)
def test_detail_retains_structured_efrsb_classifier(
    classifier: str,
    expected_category: str,
) -> None:
    html = f"""
    <html><body>
      <h1>Лот с официальным классификатором</h1>
      Информация об аукционе №0037400
      Информация о лоте №1
      Статус: Прием заявок
      Начальная цена, руб.: 100 000,00
      Классификатор ЕФРСБ:
      {classifier}
      Обеспечение заявки
      Размер обеспечения, руб.: 10 000,00
      Подробные сведения об имуществе и порядке проведения торгов.
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/52001/",
    )
    assert lot.property_type == classifier
    assert lot.raw_data["efrsb_classifier"] == classifier
    assert lot.category == expected_category
