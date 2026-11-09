from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources.zakazrf import (
    CATALOGUE_URL,
    ZakazRfReadonlyClient,
    ZakazRfSchemaError,
    classify_property,
    detail_url_for_external_id,
    external_id_from_detail_url,
    parse_catalogue_page,
    parse_current_price,
    parse_detail,
    parse_initial_price,
    parse_status,
)


REAL_ESTATE_DETAIL = """
<html><body>
<h2>Информация о лоте №1</h2>
Номер: 1
Наименование: Стояночное место - 12,5 кв. м, адрес: Республика Татарстан,
г. Казань, ул. Япеева, д. 19, № 13, подземный этаж,
кадастровый номер 16:50:010326:323
Дата начала представления заявок на участие: 14.10.2026 00:00
Статус: Прием заявок
Дата окончания представления заявок на участие: 20.11.2026 14:00
Дата проведения: 24.11.2026 00:00
Текущая цена, руб.: 1 900 548,00
Начальная цена, руб.: 1 900 548,00
Сведения об имуществе должника, его составе, характеристиках, описание:
Недвижимое имущество
Классификатор ЕФРСБ:
Здания (кроме жилых) и сооружения, не включенные в другие группировки
<table>
<tr><th>Дата начала интервала</th><th>Цена на интервале, руб.</th></tr>
<tr><td>14.10.2026 00:00</td><td>1 900 548,00</td></tr>
<tr><td>21.10.2026 00:00</td><td>1 710 493,20</td></tr>
</table>
Обеспечение заявки
</body></html>
"""


def test_public_offer_detail_uses_individual_labels_and_structured_classifier() -> None:
    lot = parse_detail(
        REAL_ESTATE_DETAIL,
        detail_url="https://bankrot.zakazrf.ru/public/public-offers/lots/view/18804/",
        external_id="zakazrf.ru:public-offers-18804",
    )

    assert lot.external_id == "zakazrf.ru:public-offers-18804"
    assert lot.start_price == 1_900_548.00
    assert lot.current_price == 1_900_548.00
    assert lot.auction_status == "active"
    assert lot.category == "real_estate"
    assert lot.cadastral_number == "16:50:010326:323"
    assert lot.area == 12.5
    assert lot.address == (
        "Республика Татарстан, г. Казань, ул. Япеева, д. 19, № 13, подземный этаж"
    )
    assert lot.application_deadline.isoformat() == "2026-11-20T14:00:00"
    assert lot.auction_at.isoformat() == "2026-11-24T00:00:00"
    assert lot.raw_data["lot_number"] == "1"
    assert lot.raw_data["structured_property_classifier_verified"] is True
    assert lot.raw_data["detail_identity_verified"] is True
    assert lot.public_offer_schedule == [
        {"starts_at": "2026-10-14T00:00:00", "price": 1_900_548.0},
        {"starts_at": "2026-10-21T00:00:00", "price": 1_710_493.2},
    ]


def test_vehicle_classifier_wins_over_unrelated_real_estate_prose() -> None:
    html = """
    <html><body>
    <h2>Информация о лоте №2</h2>
    Номер: 2
    Наименование: Автомобиль легковой, VIN XTA219110GY223704
    Дата начала представления заявок на участие: 01.02.2026 00:00
    Статус: Не состоялся
    Дата окончания представления заявок на участие: 10.02.2026 14:00
    Начальная цена, руб.: 684 000,00
    Порядок оформления содержит общее упоминание недвижимого имущества.
    Классификатор ЕФРСБ:
    Автомобили
    Обеспечение заявки
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://bankrot.zakazrf.ru/public/auctions/lots/view/18784/",
        external_id="zakazrf.ru:auctions-18784",
    )

    assert lot.category == "vehicle"
    assert lot.start_price == 684_000.00
    assert lot.current_price is None
    assert lot.auction_status == "closed"
    assert lot.vin == "XTA219110GY223704"


def test_receivables_are_not_promoted_to_property() -> None:
    html = """
    <html><body>
    <h2>Информация о лоте №1</h2>
    Номер: 1
    Наименование: Права требования по кредитным обязательствам
    Дата начала представления заявок на участие: 17.06.2026 00:00
    Статус: Не состоялся
    Дата окончания представления заявок на участие: 23.07.2026 14:00
    Текущая цена, руб.: 8 347 268,43
    Начальная цена, руб.: 20 027 035,65
    Классификатор ЕФРСБ:
    Уступка требований по кредитным обязательствам
    Интервалы снижения цены
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://bankrot.zakazrf.ru/public/public-offers/lots/view/18801/",
        external_id="zakazrf.ru:public-offers-18801",
    )

    assert lot.category == "receivables"
    assert lot.start_price == 20_027_035.65
    assert lot.current_price == 8_347_268.43


def test_deposit_and_interval_price_never_fallback_to_lot_price() -> None:
    html = """
    <html><body>
    <h2>Информация о лоте №1</h2>
    Номер: 1
    Наименование: Нежилое помещение, 80 кв. м, кадастровый номер 77:01:0001001:55
    Статус: Прием заявок
    Задаток на интервале, руб.: 150 000,00
    Цена на интервале, руб.: 900 000,00
    Классификатор ЕФРСБ:
    Здания (кроме жилых) и сооружения, не включенные в другие группировки
    Интервалы снижения цены
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://bankrot.zakazrf.ru/public/public-offers/lots/view/99999/",
        external_id="zakazrf.ru:public-offers-99999",
    )

    assert lot.start_price is None
    assert lot.current_price is None
    assert lot.category == "real_estate"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Прием заявок", "active"),
        ("Окончен", "closed"),
        ("Не состоялся", "closed"),
        ("Состоялся", "closed"),
        ("Отменен", "closed"),
        ("Приостановлен", "paused"),
        ("Прием заявок завершен", "unknown"),
        ("Завершён прием заявок", "unknown"),
    ],
)
def test_explicit_status_contract(raw: str, expected: str) -> None:
    assert parse_status(raw) == expected


def test_catalogue_is_lot_level_but_classification_fails_closed() -> None:
    href = "https://bankrot.zakazrf.ru/public/auctions/lots/view/18783/"
    html = (
        "<table><tr><td><a href='" + href + "'>"
        "Стояночное место, недвижимое имущество, 12,5 кв. м"
        "</a></td><td>Прием заявок</td></tr></table>"
    )

    lots = parse_catalogue_page(html)

    assert [lot.external_id for lot in lots] == ["zakazrf.ru:auctions-18783"]
    assert lots[0].category == "other"
    assert lots[0].raw_data["classification_requires_detail"] is True
    assert lots[0].start_price is None


def test_address_supports_provider_po_adresu_wording() -> None:
    html = """
    <html><body>
    <h2>Информация о лоте №1</h2>
    Номер: 1
    Наименование: Стояночное место - 12,5 кв. м, находящееся по адресу:
    Республика Татарстан, г. Казань, ул. Япеева, д. 19, № 13, подземный этаж,
    кадастровый номер 16:50:010326:323
    Статус: Прием заявок
    Начальная цена, руб.: 2 340 000,00
    Классификатор ЕФРСБ:
    Здания (кроме жилых) и сооружения, не включенные в другие группировки
    Обеспечение заявки
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://bankrot.zakazrf.ru/public/auctions/lots/view/18783/",
        external_id="zakazrf.ru:auctions-18783",
    )
    assert lot.address == (
        "Республика Татарстан, г. Казань, ул. Япеева, д. 19, № 13, подземный этаж"
    )


def test_title_stops_before_extended_application_date_label() -> None:
    html = """
    <html><body>
    Наименование: Электронные торги имуществом банка посредством аукциона
    Информация о лоте №1
    Номер: 1
    Наименование: Нежилое помещение - 80 кв. м, адрес: г. Москва,
    ул. Тестовая, д. 1, кадастровый номер 77:01:0001001:55
    Дата начала представления заявок на участие: 14.10.2026 00:00
    Статус: Прием заявок
    Дата окончания представления заявок на участие: 19.11.2026 14:00
    Начальная цена, руб.: 5 500 000,00
    Классификатор ЕФРСБ:
    Здания (кроме жилых) и сооружения, не включенные в другие группировки
    Обеспечение заявки
    </body></html>
    """
    lot = parse_detail(
        html,
        detail_url="https://bankrot.zakazrf.ru/public/auctions/lots/view/18805/",
        external_id="zakazrf.ru:auctions-18805",
    )

    assert lot.title.startswith("Нежилое помещение - 80 кв. м")
    assert "Электронные торги имуществом банка" not in lot.title
    assert lot.cadastral_number == "77:01:0001001:55"
    assert lot.area == 80.0
    assert lot.address == "г. Москва, ул. Тестовая, д. 1"


def test_identity_roundtrip_and_mismatch_guard() -> None:
    url = "https://bankrot.zakazrf.ru/public/public-offers/lots/view/18804/"
    external_id = "zakazrf.ru:public-offers-18804"

    assert external_id_from_detail_url(url) == external_id
    assert detail_url_for_external_id(external_id) == url

    with pytest.raises(ZakazRfSchemaError, match="identity mismatch"):
        parse_detail(
            REAL_ESTATE_DETAIL,
            detail_url=url,
            external_id="zakazrf.ru:public-offers-18805",
        )


def test_price_helpers_require_exact_labels() -> None:
    text = (
        "Информация о лоте №1 Номер: 1 "
        "Задаток, руб.: 300 000,00 Цена на интервале, руб.: 900 000,00"
    )
    assert parse_initial_price(text) is None
    assert parse_current_price(text) is None


def test_structured_classifier_unknown_fails_closed() -> None:
    assert classify_property("Ценные бумаги") is None


class FakeResponse:
    def __init__(self, content: bytes, url: str = CATALOGUE_URL, status_code: int = 200):
        self.content = content
        self.url = url
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.posts: list[tuple[str, dict[str, str]]] = []

    def _next(self) -> FakeResponse:
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)

    def get(self, url: str, **kwargs):
        return self._next()

    def post(self, url: str, **kwargs):
        self.posts.append((url, dict(kwargs.get("data") or {})))
        return self._next()


def test_postback_pager_crosses_ten_page_group() -> None:
    page_nine = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state9'>
      <a href="javascript:__doPostBack('grid$page10','')">10</a>
    </form>
    """
    page_ten = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state10'>
      <a href='/public/auctions/lots/view/19990/'>Page ten apartment</a>
      <a href="javascript:__doPostBack('grid$nextGroup','')">&gt;&gt;</a>
    </form>
    """
    page_eleven = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state11'>
      <a href='/public/auctions/lots/view/19991/'>Page eleven apartment</a>
    </form>
    """
    session = FakeSession(
        [
            FakeResponse(page_nine),
            FakeResponse(page_ten),
            FakeResponse(page_eleven),
        ]
    )
    client = ZakazRfReadonlyClient(session=session)

    page10 = client.search_page(10)
    assert [lot.external_id for lot in page10.items] == ["zakazrf.ru:auctions-19990"]
    assert page10.next_page == 11

    page11 = client.search_page(11)
    assert [lot.external_id for lot in page11.items] == ["zakazrf.ru:auctions-19991"]
    assert page11.next_page is None
    assert session.posts[0][1]["__EVENTTARGET"] == "grid$page10"
    assert session.posts[1][1]["__EVENTTARGET"] == "grid$nextGroup"
    assert session.posts[1][1]["__VIEWSTATE"] == "state10"
