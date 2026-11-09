from __future__ import annotations

import asyncio

import pytest

from bankrotai.connectors.etp_sources import (
    PROFILES,
    SOURCE_CONNECTOR_TYPES,
    parse_detail_html,
    parse_listing_html,
)
from bankrotai.connectors.etp_sources.source_specific import (
    etpu_category,
    etpu_classifier_flags,
)


def _profile():
    return next(item for item in PROFILES if item.source_id == "etpu.ru")


def _detail(
    *,
    classifier: str,
    title: str = "Земельный участок с нежилым зданием",
    start_price: str = "1 234 567,89",
    current_price: str | None = None,
    status: str = "Прием заявок",
) -> str:
    current = f"Текущая цена, руб.: {current_price} " if current_price is not None else ""
    return f"""
    <html><body>
      <h1>Информация об аукционе №0055237</h1>
      <section>
        Информация о лоте № 1
        Наименование: {title}
        Статус: {status}
        Дата начала представления заявок (Московское время): 09.11.2026 10:00:00
        Дата окончания представления заявок (Московское время): 30.11.2026 10:00:00
        Дата проведения (Московское время): 04.12.2026 12:00:00
        Начальная цена, руб.: {start_price}
        {current}
        Размер задатка, руб.: 246 913,58
        Шаг аукциона, руб.: 61 728,39
        Классификатор ЕФРСБ: {classifier}
        Документы
      </section>
    </body></html>
    """


def test_etpu_detail_uses_individual_labelled_price_status_and_classifier() -> None:
    parsed = parse_detail_html(
        _profile(),
        _detail(classifier="Земельные участки; Здания (помещения) жилые, не входящие в жилищный фонд"),
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143436/",
    )

    assert parsed.external_id == "etpu.ru:auctions-143436"
    assert parsed.start_price == 1_234_567.89
    assert parsed.current_price is None
    assert parsed.auction_status == "active"
    assert parsed.procedure_number == "0055237"
    assert parsed.category == "real_estate"
    assert parsed.raw_data["efrsb_classifier_flags"] == ["land", "real_estate"]
    assert parsed.raw_data["structured_property_classification"] is True
    assert parsed.raw_data["detail_initial_price_verified"] is True
    assert parsed.application_deadline.isoformat() == "2026-11-30T10:00:00"
    assert parsed.auction_at.isoformat() == "2026-12-04T12:00:00"
    assert parsed.auction_timezone == "Europe/Moscow"


def test_etpu_public_offer_keeps_initial_and_current_prices_distinct() -> None:
    parsed = parse_detail_html(
        _profile(),
        _detail(
            classifier="Права требования к физическим лицам",
            title="Право требования по договору займа",
            start_price="900 000,00",
            current_price="540 000,00",
        ),
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143438/",
    )

    assert parsed.external_id == "etpu.ru:public-offers-143438"
    assert parsed.start_price == 900_000
    assert parsed.current_price == 540_000
    assert parsed.category == "receivables"


def test_etpu_does_not_invent_price_from_deposit_or_interval_amount() -> None:
    html = """
    <html><body>
      Информация о публичном предложении №0056000
      Информация о лоте № 1
      Наименование: Земельный участок
      Статус: Прием заявок
      Дата окончания представления заявок (Московское время): 30.11.2026 10:00:00
      Размер задатка, руб.: 100 000,00
      Цена на интервале, руб.: 750 000,00
      Классификатор ЕФРСБ: Земельные участки
      Интервалы снижения цены
    </body></html>
    """
    parsed = parse_detail_html(
        _profile(),
        html,
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143422/",
    )

    assert parsed.start_price is None
    assert parsed.current_price is None


@pytest.mark.parametrize(
    ("classifier", "expected"),
    [
        ("Здания (помещения) нежилые", "real_estate"),
        ("Земельные участки", "land"),
        ("Права долевой собственности", "shares"),
        ("Право аренды земельного участка", "lease"),
        ("Автомобили", "vehicle"),
        ("Производственное, промышленное оборудование", "equipment"),
        ("Права требования к физическим лицам", "receivables"),
    ],
)
def test_etpu_structured_efrsb_categories(classifier: str, expected: str) -> None:
    text = f"Информация о лоте № 1 Классификатор ЕФРСБ: {classifier} Документы"
    assert etpu_category(text) == expected
    assert expected in etpu_classifier_flags(text)


def test_etpu_official_other_classifier_is_not_overridden_by_free_text() -> None:
    parsed = parse_detail_html(
        _profile(),
        _detail(classifier="Прочее", title="Автомобиль легковой, VIN XTA00000000000000"),
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143411/",
    )

    assert parsed.category == "other"
    assert parsed.raw_data["efrsb_classifier"] == "Прочее"
    assert parsed.raw_data["efrsb_classifier_flags"] == ["other"]


def test_etpu_multi_lot_catalogue_keeps_individual_lot_ids_and_prices() -> None:
    html = """
    <table>
      <tr><th>Торги</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0055237</td>
        <td><a href="/public/auctions/lots/view/143434/">Лот 1. Помещение</a></td>
        <td>2 500 000,00</td><td>Прием заявок</td></tr>
      <tr><td>0055237</td>
        <td><a href="/public/auctions/lots/view/143435/">Лот 2. Земельный участок</a></td>
        <td>900 000,00</td><td>Прием заявок</td></tr>
    </table>
    """
    lots = parse_listing_html(_profile(), html)

    assert [lot.external_id for lot in lots] == [
        "etpu.ru:auctions-143434",
        "etpu.ru:auctions-143435",
    ]
    assert [lot.start_price for lot in lots] == [2_500_000, 900_000]


class _Response:
    status_code = 200
    url = "https://bankrupt.etpu.ru/public/auctions/lots/view/143436/"

    def __init__(self, body: str) -> None:
        self.content = body.encode()

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, body: str) -> None:
        self.body = body
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def get(self, url, *_args, **_kwargs):
        self.requested.append(url)
        return _Response(self.body)


def test_etpu_fetch_lot_reconstructs_exact_individual_detail_url() -> None:
    session = _Session(_detail(classifier="Земельные участки"))
    connector = SOURCE_CONNECTOR_TYPES["etpu.ru"](session=session)
    parsed = asyncio.run(connector.fetch_lot("etpu.ru:auctions-143436"))

    assert session.requested == [
        "https://bankrupt.etpu.ru/public/auctions/lots/view/143436/"
    ]
    assert parsed.external_id == "etpu.ru:auctions-143436"
    assert "detail_enrichment" in connector.capabilities
