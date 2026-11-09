from __future__ import annotations

import asyncio
import json

import pytest
import requests

from bankrotai.connectors.etp_sources import (
    PROFILES,
    SOURCE_CONNECTOR_TYPES,
    SourceAccessError,
    SourceSchemaError,
    parse_detail_html,
    parse_listing_html,
)
from bankrotai.connectors.registry import connector_registry


def profile(source_id: str):
    return next(item for item in PROFILES if item.source_id == source_id)


@pytest.mark.parametrize(
    ("source_id", "href", "title"),
    [
        ("m-ets.ru", "https://m-ets.ru/243003-1", "Жилой дом 78 кв.м., кадастровый номер 50:01:001:123, 4 500 000 руб."),
        ("arbbitlot.ru", "https://torgi.arbbitlot.ru/public/auctions/lots/view/44914/", "Автомобиль LADA GRANTA, VIN XTA219110GY223704, 350 000 руб."),
        ("zakazrf.ru", "https://bankrot.zakazrf.ru/public/public-offers/lots/view/18797/", "Стояночное место 12,5 кв. м, кадастровый номер 16:50:010326:323"),
        ("fabrikant.ru", "https://fabrikant.ru/v2/trades/procedure/view/RoEMbNetsW8SvO2KLb7GQA", "Земельный участок 1 050 кв. м, 900 000 руб."),
    ],
)
def test_profile_parser_extracts_stable_identity_and_fields(source_id: str, href: str, title: str) -> None:
    html = f"<table><tr><td><a href='{href}'>{title}</a></td><td>Прием заявок</td></tr></table>"
    lots = parse_listing_html(profile(source_id), html)
    assert len(lots) == 1
    assert lots[0].external_id.startswith(source_id + ":")
    assert lots[0].lot_url == href
    assert lots[0].raw_data["safe_for_archival"] is False
    assert lots[0].raw_data["reconcile_missing"] is False



@pytest.mark.parametrize("href", [
    "http://bankrupt.etpu.ru/public/auctions/lots/view/224/",
    "https://user:secret@bankrupt.etpu.ru/public/auctions/lots/view/224/",
    "https://bankrupt.etpu.ru:8443/public/auctions/lots/view/224/",
    "https://evil.example/public/auctions/lots/view/224/",
])
def test_listing_links_must_use_trusted_https_endpoint(href: str) -> None:
    html = f"<a href='{href}'>Земельный участок</a>"
    assert parse_listing_html(profile("etpu.ru"), html) == []


def test_sberbank_json_rejects_untrusted_lot_link() -> None:
    from bankrotai.connectors.etp_sources import parse_sberbank_json
    raw = {
        "objectHref": "http://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/12/0/0/101",
        "title": "Земельный участок",
    }
    with pytest.raises(SourceAccessError, match="unsafe provider URL"):
        parse_sberbank_json(
            profile("sberbank-ast.ru"), {"PurchaseList": [{"_source": raw}]}, page=1
        )


def test_ruson_detail_enrichment_reads_labelled_price_and_dates_without_rub_suffix() -> None:
    html = """
    <html><body><h1>Нежилое помещение 50:46:0060406:241</h1>
    Начальная цена 5205000.00
    Дата окончания приема заявок 19.08.2022 18:00
    Дата проведения 22.08.2022 10:00
    Торги не состоялись
    Официальная карточка содержит подробное описание продаваемого имущества.
    </body></html>
    """
    parsed = parse_detail_html(
        profile("rus-on.ru"),
        html,
        detail_url="https://rus-on.ru/bankrot/lot_view.php?lot_nid=27424",
        external_id="rus-on.ru:27424",
    )

    assert parsed.start_price == 5_205_000
    assert parsed.application_deadline.isoformat() == "2022-08-19T18:00:00"
    assert parsed.auction_at.isoformat() == "2022-08-22T10:00:00"
    assert parsed.auction_status == "closed"
    assert "detail_enrichment" in SOURCE_CONNECTOR_TYPES["rus-on.ru"].capabilities


def test_duplicate_links_do_not_duplicate_lots() -> None:
    href = "https://rus-on.ru/bankrot/lot_view.php?lot_nid=115011"
    html = f"<div><a href='{href}'>Лот 1</a><a href='{href}'>Земельный участок площадью 792 кв. м.</a></div>"
    lots = parse_listing_html(profile("rus-on.ru"), html)
    assert len(lots) == 1
    assert lots[0].title.startswith("Земельный участок")


def test_clickable_table_row_is_parsed_without_executing_javascript() -> None:
    html = """
    <table><tr onclick="window.location='/trade_view.php?trade_nid=66627'">
      <td>66627-ОАОФ</td><td>Организатор</td>
      <td>Должник Всего лотов: 1 Лот № 1: Земельный участок площадью 900 кв. м.</td>
      <td>Торги объявлены</td>
    </tr></table>
    """
    lots = parse_listing_html(profile("sistematorg.com"), html)
    assert [lot.external_id for lot in lots] == ["sistematorg.com:66627:1"]
    assert lots[0].auction_status == "unknown"
    assert lots[0].start_price is None


def test_unknown_values_remain_null_and_status_unknown() -> None:
    html = "<a href='https://m-ets.ru/243003-1'>Имущественный комплекс</a>"
    lot = parse_listing_html(profile("m-ets.ru"), html)[0]
    assert lot.start_price is None
    assert lot.current_price is None
    assert lot.auction_status == "unknown"


def test_all_profiles_are_registered() -> None:
    assert set(SOURCE_CONNECTOR_TYPES) <= set(connector_registry.source_ids())


def test_registry_uses_new_parsers_except_for_mature_source_specific_connectors() -> None:
    preserved = {"m-ets.ru", "cdtrf.ru", "rts-tender.ru"}
    for source_id, connector_cls in SOURCE_CONNECTOR_TYPES.items():
        if source_id not in preserved:
            assert isinstance(connector_registry.create(source_id), connector_cls)


@pytest.mark.parametrize("source_id", ["rts-tender.ru", "utender.ru", "electro-torgi.ru"])
def test_unproven_sources_fail_explicitly(source_id: str) -> None:
    connector = SOURCE_CONNECTOR_TYPES[source_id]()
    with pytest.raises(SourceAccessError):
        asyncio.run(connector.search({}))
    health = asyncio.run(connector.healthcheck())
    assert health.status == "blocked"


def test_profit_listing_has_stable_kind_trade_lot_identity_and_pagination() -> None:
    html = """
    <article><p>Лот №1</p><a href="/otpp/22740/lots/40662">
    Земельный участок 22:41:040210:35</a><span>Начальная цена 750 000 руб.</span></article>
    <a href="/lots?page=2">2</a>
    """
    item = parse_listing_html(profile("etp-profit.ru"), html)[0]
    assert item.external_id == "etp-profit.ru:otpp-22740-40662"
    assert item.start_price == 750_000
    assert item.cadastral_number == "22:41:040210:35"


def test_profit_detail_accepts_its_labelled_price_without_currency_suffix() -> None:
    detail = """
    <html><body><h1>Земельный участок 34:28:040003:1165</h1>
    Начальная цена продажи 1 398 600. 00
    Статус торгов по лоту объявлен. Окончание приема заявок 23.11.2026 11:00:00.
    Полная публичная карточка имущества содержит описание и условия продажи.
    </body></html>
    """
    parsed = parse_detail_html(
        profile("etp-profit.ru"), detail,
        detail_url="https://etp-profit.ru/otpp/23376/lots/41432",
    )
    assert parsed.start_price == 1_398_600
    assert parsed.auction_status == "scheduled"


def test_ausib_onclick_listing_and_single_lot_detail_are_enriched() -> None:
    listing = """
    <table><tr onclick="window.location='/trade/view/purchase/general.html?id=104685887'">
      <td>17320-ОТПП</td><td>Управляющий</td>
      <td>Нежилое помещение 27.8 кв.м., 27:23:0050709:453</td>
      <td>объявлены</td><td>03.11.2026 07:00</td>
    </tr></table><a href="?page=2">2</a>
    """
    item = parse_listing_html(profile("ausib.ru"), listing)[0]
    assert item.external_id == "ausib.ru:104685887"
    detail = """
    <html><body><table id="lotNumber1"><tr><td>Предмет торгов</td>
    <td>Нежилое помещение 27.8 кв.м., 27:23:0050709:453</td></tr>
    <tr><td>Начальная цена продажи имущества</td><td>293 000,00 руб.</td></tr>
    <tr><td>Статус торгов</td><td>объявлены</td></tr></table>
    Дополнительные публичные сведения карточки процедуры и условия продажи имущества.
    </body></html>
    """
    parsed = parse_detail_html(
        profile("ausib.ru"), detail,
        detail_url="https://ausib.ru/trade/view/purchase/general.html?id=104685887",
        external_id=item.external_id,
    )
    assert parsed.start_price == 293_000
    assert parsed.cadastral_number == "27:23:0050709:453"
    assert parsed.auction_status == "scheduled"


def test_ausib_multi_lot_procedure_fails_closed() -> None:
    html = "<html><body>" + ("Карточка процедуры " * 10) + """
      <table id="lotNumber1"><tr><td>Предмет торгов</td><td>Квартира</td></tr></table>
      <table id="lotNumber2"><tr><td>Предмет торгов</td><td>Гараж</td></tr></table>
    </body></html>"""
    with pytest.raises(SourceSchemaError, match="exactly one"):
        parse_detail_html(
            profile("ausib.ru"), html,
            detail_url="https://ausib.ru/trade/view/purchase/general.html?id=104685887",
        )


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", url: str = "https://m-ets.ru/search") -> None:
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


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_access_failures_are_not_reported_as_empty_success(
    status_code: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=FakeSession(FakeResponse(status_code)))
    monkeypatch.setattr(connector, "RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    with pytest.raises(SourceAccessError, match=str(status_code)):
        asyncio.run(connector.search({}))


def test_transient_etp_timeout_is_retried_without_skipping_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"<a href='https://m-ets.ru/243003-1'>Zemelniy uchastok 50:01:001:123</a>"
    session = FakeSession([
        requests.Timeout("temporary"),
        FakeResponse(200, body, "https://m-ets.ru/search"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=session)
    monkeypatch.setattr(connector, "RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    result = asyncio.run(connector.search({}))
    assert [lot.external_id for lot in result.items] == ["m-ets.ru:243003-1"]
    assert len(session.requested_urls) == 2


def test_timeout_marks_health_as_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=FakeSession(requests.Timeout("slow provider")))
    monkeypatch.setattr(connector, "RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    health = asyncio.run(connector.healthcheck())
    assert health.status == "degraded"
    assert "slow provider" in health.message


def test_schema_drift_is_not_reported_as_empty_success() -> None:
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](
        session=FakeSession(FakeResponse(200, b"<html><body>new unknown markup</body></html>"))
    )
    with pytest.raises(RuntimeError, match="no lot links"):
        asyncio.run(connector.search({}))


def test_detail_parser_uses_labelled_price_before_deposit() -> None:
    html = """
    <html><body><h1>Жилой дом и земельный участок</h1>
    5 948 100 руб. Начальная цена 594 810 руб. Размер задатка.
    Кадастровый номер: 50:18:0080111:1897. Окончание приема заявок: 04.12.2026 09:00.
    Публичная карточка содержит достаточное описание лота для проверки парсинга.
    </body></html>
    """.encode()
    lot = parse_detail_html(
        profile("m-ets.ru"), html, detail_url="https://m-ets.ru/243003-1"
    )
    assert lot.start_price == 5_948_100
    assert lot.cadastral_number == "50:18:0080111:1897"


def test_price_label_does_not_capture_preceding_deposit() -> None:
    html = """
    <html><body><h1>Земельный участок</h1>
    Размер задатка 100 000 руб. Начальная цена 1 000 000 руб.
    Публичная карточка содержит достаточное описание лота для проверки парсинга.
    </body></html>
    """.encode()
    lot = parse_detail_html(
        profile("m-ets.ru"), html, detail_url="https://m-ets.ru/243003-1"
    )
    assert lot.start_price == 1_000_000


def test_price_label_keeps_price_before_following_deposit_field() -> None:
    html = """
    <html><body><h1>Земельный участок</h1>
    Начальная цена 1 000 000 руб. Размер задатка 100 000 руб.
    Публичная карточка содержит достаточное описание лота для проверки парсинга.
    </body></html>
    """.encode()
    lot = parse_detail_html(
        profile("m-ets.ru"), html, detail_url="https://m-ets.ru/243003-1"
    )
    assert lot.start_price == 1_000_000



@pytest.mark.parametrize("source_id, href", [
    ("etpu.ru", "https://bankrupt.etpu.ru/public/auctions/lots/view/224/"),
    ("centerr.ru", "https://bankrupt.centerr.ru/public/auctions/lots/view/224/"),
    ("utpl.ru", "https://bankrupt.utpl.ru/public/auctions/lots/view/224/"),
    ("zakazrf.ru", "https://bankrot.zakazrf.ru/public/auctions/lots/view/224/"),
])
def test_table_start_price_without_currency_uses_verified_header(source_id: str, href: str) -> None:
    html = f"""
    <table><tr><th>№</th><th>Лот</th><th>Начальная цена</th>
    <th>Задаток</th></tr><tr>
    <td>1</td><td><a href="{href}">Земельный участок 50:05:0010203:1234</a></td>
    <td>2 500 000,25</td><td>500 000,00</td></tr></table>
    """
    lot = parse_listing_html(profile(source_id), html)[0]
    assert lot.start_price == 2500000.25
    assert lot.current_price is None


def test_unlabelled_amount_does_not_become_price_without_a_table_header() -> None:
    html = """
    <table><tr><td>Лот</td><td>Стоимость</td></tr>
    <tr><td><a href="/public/auctions/lots/view/224/">Земельный участок</a></td>
    <td>2 500 000,25</td></tr></table>
    """
    lot = parse_listing_html(profile("etpu.ru"), html)[0]
    assert lot.start_price is None
    assert lot.current_price is None


def test_explicit_current_price_is_not_confused_with_initial_or_deposit() -> None:
    body = """
    <html><body><h1>Земельный участок в Московской области</h1>
    Начальная цена 1 000 000 руб. Текущая цена 800 000 руб.
    Размер задатка 100 000 руб. Кадастровый номер: 50:05:0010203:1234.
    Подробное описание объекта продажи с открытым доступом для всех участников.
    </body></html>
    """
    detail = parse_detail_html(
        profile("m-ets.ru"), body, detail_url="https://m-ets.ru/243003-1"
    )
    assert detail.start_price == 1000000
    assert detail.current_price == 800000
    assert detail.deposit_amount is None
    listing = (
        "<table><tr><td><a href='https://m-ets.ru/243003-1'>Земельный участок</a></td>"
        "<td>Начальная цена 1 000 000 руб. Текущая цена 800 000 руб.</td></tr></table>"
    )
    entry = parse_listing_html(profile("m-ets.ru"), listing)[0]
    assert entry.start_price == 1000000
    assert entry.current_price == 800000


def test_start_price_does_not_imply_a_current_price() -> None:
    body = """
    <html><body><h1>Земельный участок</h1>
    Начальная цена 1 000 000 руб. Размер задатка 100 000 руб.
    Текст открытой карточки земельного участка, описание имущества.
    </body></html>
    """
    lot = parse_detail_html(profile("m-ets.ru"), body, detail_url="https://m-ets.ru/243003-1")
    assert lot.start_price == 1000000
    assert lot.current_price is None



def test_missing_labeled_price_is_not_filled_from_adjacent_field() -> None:
    detail_url = "https://m-ets.ru/243003-1"
    prefix = (
        "<html><body><h1>Земельный участок с кадастровым номером</h1>"
        "Подробное публичное описание предмета торгов и условий продажи. "
    )
    first = parse_detail_html(
        profile("m-ets.ru"),
        prefix + "Начальная цена: Текущая цена 800 000 руб. Размер задатка 100 000 руб.</body></html>",
        detail_url=detail_url,
    )
    assert first.start_price is None
    assert first.current_price == 800000

    second = parse_detail_html(
        profile("m-ets.ru"),
        prefix + "Текущая цена: Размер задатка 100 000 руб.</body></html>",
        detail_url=detail_url,
    )
    assert second.start_price is None
    assert second.current_price is None


def test_sberbank_prefers_explicit_ruble_amount_and_does_not_invent_current() -> None:
    from bankrotai.connectors.etp_sources import parse_sberbank_json
    base = {
        "objectHref": "https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/12/0/0/101",
        "title": "Земельный участок, кадастровый номер 47:16:0420001:37",
        "purchAmountRUB": "722100",
        "purchAmount": "900000",
    }
    lots = parse_sberbank_json(profile("sberbank-ast.ru"), {"PurchaseList": [{"_source": base}]}, page=1)
    assert len(lots) == 1
    assert lots[0].start_price == 722100
    assert lots[0].current_price is None
    assert lots[0].auction_at is None

    unknown_currency = dict(base, purchAmountRUB=None)
    assert parse_sberbank_json(
        profile("sberbank-ast.ru"), {"PurchaseList": [{"_source": unknown_currency}]}, page=1
    )[0].start_price is None

    rubles = dict(unknown_currency, purchCurrency="RUB")
    assert parse_sberbank_json(
        profile("sberbank-ast.ru"), {"PurchaseList": [{"_source": rubles}]}, page=1
    )[0].start_price == 900000


def _nistp_public_lot_row(
    trade_id: str, code: str, number: int, title: str,
    price: str = "3 274 000.00",
) -> str:
    """Matches the seven-column view_type=lot HTML saved from NISTP."""
    return (
        f"<tr><td><a href='/bankrot/trade_view.php?trade_nid={trade_id}'>{code}</a></td>"
        "<td>Организатор</td>"
        f"<td>Должник Лот № {number} : {title}</td>"
        f"<td>{price}</td><td>11.11.2026</td><td>16.12.2026</td>"
        "<td>Торги объявлены</td></tr>"
    )


def test_nistp_multi_lot_trade_preserves_each_property_identity() -> None:
    html = "<table>" + "".join([
        _nistp_public_lot_row("1234", "71874-ОАОФ", 1, "Земельный участок 76:12:0000123:456", "2 500 000.25"),
        _nistp_public_lot_row("1234", "71874-ОАОФ", 2, "Автомобиль BMW", "450 000.00"),
        _nistp_public_lot_row("1234", "71874-ОАОФ", 3, "Квартира 77:01:0001010:1234"),
    ]) + "</table>"
    lots = parse_listing_html(profile("nistp.ru"), html)
    assert [lot.external_id for lot in lots] == [
        "nistp.ru:1234:1",
        "nistp.ru:1234:3",
    ]
    assert [lot.start_price for lot in lots] == [2500000.25, 3274000.0]
    assert [lot.auction_status for lot in lots] == ["scheduled", "scheduled"]
    assert lots[0].cadastral_number == "76:12:0000123:456"
    assert lots[1].cadastral_number == "77:01:0001010:1234"


def test_nistp_parser_does_not_apply_preview_limit_to_full_page() -> None:
    rows = "".join(
        _nistp_public_lot_row(
            str(index), f"{70000 + index}-ОАОФ", 1,
            f"Земельный участок 76:12:0000123:{1000 + index}",
        )
        for index in range(1, 36)
    )
    lots = parse_listing_html(profile("nistp.ru"), f"<table>{rows}</table>")
    assert len(lots) == 35


def test_nistp_shared_trade_card_exposes_per_lot_detail_capability() -> None:
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"]()
    assert "fetch_lot" in connector.capabilities
    assert "detail_enrichment" in connector.capabilities
    with pytest.raises(SourceSchemaError, match="invalid per-lot external_id"):
        asyncio.run(connector.fetch_lot("nistp.ru:1234"))


def test_fetch_lot_reconstructs_shared_engine_detail_url() -> None:
    body = """
    <html><body><h1>Квартира площадью 59 кв. м.</h1>
    Начальная цена 2 000 000 руб. Кадастровый номер 64:44:090102:713.
    Публичная карточка содержит достаточное описание лота для проверки.
    </body></html>
    """.encode()
    session = FakeSession(FakeResponse(200, body))
    connector = SOURCE_CONNECTOR_TYPES["zakazrf.ru"](session=session)
    lot = asyncio.run(connector.fetch_lot("zakazrf.ru:public-offers-18790"))
    assert session.requested_urls == [
        "https://bankrot.zakazrf.ru/public/public-offers/lots/view/18790/"
    ]
    assert lot.external_id == "zakazrf.ru:public-offers-18790"


def test_form_pagination_posts_page_number() -> None:
    page_one = b"""
    <form id='nodesearch' method='post' action='/bankrot/trade_list.php'>
      <input type='hidden' name='pagenum' value='1'>
      <a onclick='pagenum_send(2)'>2</a>
    </form>
    """
    page_two = ("<table>" + _nistp_public_lot_row(
        "501500", "71874-ОАОФ", 1, "Земельный участок 76:12:0000123:456",
    ) + "</table>").encode()
    session = FakeSession([
        FakeResponse(200, page_one, "https://nistp.ru/bankrot/trade_list.php"),
        FakeResponse(200, page_two, "https://nistp.ru/bankrot/trade_list.php"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    result = asyncio.run(connector.search({}, cursor="2"))
    assert result.items[0].external_id == "nistp.ru:501500:1"
    assert session.posts[0][1]["pagenum"] == "2"


def test_nistp_vehicle_only_page_is_traversed_without_property_ingestion() -> None:
    page_one = b"""
    <form id='nodesearch' method='post' action='/bankrot/trade_list.php'>
      <input type='hidden' name='pagenum' value='1'>
      <a onclick='pagenum_send(2)'>2</a>
    </form>
    """
    page_two = (
        "<table>"
        + _nistp_public_lot_row("498758", "71271-ОАОФ", 4, "Автомобиль LADA")
        + "</table><a onclick='pagenum_send(3)'>3</a>"
    ).encode()
    session = FakeSession([
        FakeResponse(200, page_one, "https://nistp.ru/bankrot/trade_list.php"),
        FakeResponse(200, page_two, "https://nistp.ru/bankrot/trade_list.php"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)

    result = asyncio.run(connector.search({}, cursor="2"))

    assert result.items == []
    assert result.next_cursor == "3"


def test_form_pagination_rejects_cross_host_action() -> None:
    page_one = b"""
    <form id='nodesearch' action='https://127.0.0.1/internal'>
      <input type='hidden' name='pagenum' value='1'>
    </form>
    """
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](
        session=FakeSession(FakeResponse(200, page_one, "https://nistp.ru/bankrot/trade_list.php"))
    )
    with pytest.raises(SourceAccessError, match="unsafe provider URL"):
        asyncio.run(connector.search({}, cursor="2"))


def test_aspnet_pagination_posts_viewstate_and_target() -> None:
    page_one = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state'>
      <a href="javascript:__doPostBack('grid$page2','')">2</a>
    </form>
    """
    page_two = b"<a href='/public/auctions/lots/view/18800/'>Apartment on page two</a>"
    session = FakeSession([
        FakeResponse(200, page_one, "https://bankrot.zakazrf.ru/public/purchases-all/"),
        FakeResponse(200, page_two, "https://bankrot.zakazrf.ru/public/purchases-all/"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["zakazrf.ru"](session=session)
    result = asyncio.run(connector.search({}, cursor="2"))
    assert result.items[0].external_id == "zakazrf.ru:auctions-18800"
    assert session.posts[0][1]["__VIEWSTATE"] == "state"
    assert session.posts[0][1]["__EVENTTARGET"] == "grid$page2"


def test_sberbank_api_pagination_and_mapping() -> None:
    payload = {
        "Total": 22,
        "PurchaseList": [{
            "_source": {
                "objectHref": "https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/8/0/0/53571",
                "title": "Автомобиль LADA VESTA, VIN XTAGFK110NY607362",
                "purchName": "Торги по реализации автомобиля",
                "purchAmount": "890000",
                "purchCurrency": "RUB",
                "PurchaseStageTerm": "Прием заявок",
                "purchCode": "SBR013-2610260004-0",
                "RegionNameTerm": "Г. Санкт-Петербург",
                "Address": "Г. Санкт-Петербург",
                "RequestStopDate": "03.12.2026 10:00",
                "AuctionResultDate": "07.12.2026 17:00",
            }
        }],
    }
    session = FakeSession(FakeResponse(200, json.dumps(payload).encode()))
    connector = SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"](session=session)
    result = asyncio.run(connector.search({}))
    assert result.next_cursor == "2"
    assert result.items[0].external_id == "sberbank-ast.ru:8-0-0-53571"
    assert result.items[0].start_price == 890000
    assert result.items[0].vin == "XTAGFK110NY607362"
    assert session.posts[0][0] == "https://www.sberbank-ast.ru/api/Processing/main"


def test_sberbank_healthcheck_uses_json_contract() -> None:
    payload = {
        "Total": 1,
        "PurchaseList": [{"_source": {
            "objectHref": "https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/8/0/0/53571",
            "title": "Автомобиль LADA VESTA",
        }}],
    }
    connector = SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"](
        session=FakeSession(FakeResponse(200, json.dumps(payload).encode()))
    )
    health = asyncio.run(connector.healthcheck())
    assert health.status == "ok"


def test_sistematorg_trade_level_listing_does_not_assign_multi_lot_price() -> None:
    html = """
    <table><tr onclick="window.location='/trade_view.php?trade_nid=19431'">
      <td>19431-ОАОФ</td>
      <td>Всего лотов: 2. Лот № 1: автомобиль. Лот № 2: Земельный участок.
          Начальная цена 800 000 руб.</td>
      <td>Торги объявлены</td>
    </tr></table>
    """
    lots = parse_listing_html(profile("sistematorg.com"), html)
    assert [lot.external_id for lot in lots] == ["sistematorg.com:19431:2"]
    assert lots[0].start_price is None
    assert lots[0].raw_data["procedure_id"] == "19431"
    assert lots[0].raw_data["per_lot_detail_verified"] is False
    assert lots[0].current_price is None
    assert lots[0].raw_data["trade_level_listing"] is True
    assert lots[0].raw_data["reported_trade_lots"] == 2
    assert lots[0].raw_data["individual_lot_identity_verified"] is False


def test_sistematorg_detail_rejects_unattributable_multi_lot_price() -> None:
    html = """
    <html><body><h1>Торги по продаже имущества</h1>
    Всего лотов: 2. Лот № 1: автомобиль, начальная цена 100 000 руб.
    Лот № 2: земельный участок, начальная цена 900 000 руб.
    Официальная карточка процедуры, имущество должника и периоды заявок.
    </body></html>
    """
    from bankrotai.connectors.etp_sources import SourceSchemaError
    with pytest.raises(SourceSchemaError, match="cannot attribute a trade-level price"):
        parse_detail_html(
            profile("sistematorg.com"), html,
            detail_url="https://sistematorg.com/trade_view.php?trade_nid=19431",
        )


def test_sistematorg_single_lot_trade_price_remains_source_evidence() -> None:
    html = """
    <html><body><h1>Торги по продаже земельного участка</h1>
    Всего лотов: 1. Лот № 1: земельный участок 45:12:080101:100.
    Начальная цена 900 000 руб. Размер задатка 90 000 руб.
    Официальная карточка процедуры с подробным описанием имущества.
    </body></html>
    """
    lot = parse_detail_html(
        profile("sistematorg.com"), html,
        detail_url="https://sistematorg.com/trade_view.php?trade_nid=19433",
    )
    assert lot.start_price == 900_000

def test_sistematorg_single_lot_connector_fetches_verified_trade_detail() -> None:
    detail = """
    <html><body><h1>Торги по продаже квартиры</h1>
    Всего лотов: 1. Лот № 1: квартира 50:16:0302009:4559.
    Начальная цена 4 609 800.00 руб. Размер задатка 460 980 руб.
    Официальная карточка процедуры с подробным описанием имущества.
    </body></html>
    """.encode("utf-8")
    session = FakeSession(FakeResponse(
        200, detail, "https://sistematorg.com/trade_view.php?trade_nid=36750"
    ))
    connector = SOURCE_CONNECTOR_TYPES["sistematorg.com"](session=session)

    parsed = asyncio.run(connector.fetch_lot("sistematorg.com:36750:1"))

    assert parsed.external_id == "sistematorg.com:36750:1"
    assert parsed.start_price == 4_609_800
    assert "fetch_lot" in connector.capabilities
    assert session.requested_urls == [
        "https://sistematorg.com/trade_view.php?trade_nid=36750"
    ]

def test_repeated_catalogue_response_on_second_page_fails_closed() -> None:
    page = b"<a href='https://m-ets.ru/243003-1'>Zemelniy uchastok v prodazhe</a>"
    session = FakeSession([
        FakeResponse(200, page, "https://m-ets.ru/search"),
        FakeResponse(200, page, "https://m-ets.ru/search?page=2"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=session)
    first = asyncio.run(connector.search({}))
    assert len(first.items) == 1
    with pytest.raises(RuntimeError, match="repeated catalogue page"):
        asyncio.run(connector.search({}, cursor="2"))


def test_empty_later_catalogue_page_does_not_claim_source_end() -> None:
    page = b"<a href='https://m-ets.ru/243003-1'>Zemelniy uchastok v prodazhe</a>"
    session = FakeSession([
        FakeResponse(200, page, "https://m-ets.ru/search"),
        FakeResponse(200, b"<html>empty page</html>", "https://m-ets.ru/search?page=2"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=session)
    assert len(asyncio.run(connector.search({})).items) == 1
    with pytest.raises(RuntimeError, match="no lot links"):
        asyncio.run(connector.search({}, cursor="2"))



def test_sistematorg_separates_procedure_into_individual_real_estate_lots():
    html = """
    <table><tr>
      <td><a href="/trade_view.php?trade_nid=19431">19431-ОАОФ</a></td>
      <td>Организатор</td>
      <td>Должник Всего лотов: 3
          Лот № 1: Автомобиль КИА РИО, цена 400 000 руб.
          Лот № 2: Земельный участок 12:06:4601002:152
          Лот № 3: Квартира 12:05:0702001:1292</td>
      <td>Торги объявлены</td><td>07.08.2026</td>
    </tr></table>
    """
    lots = parse_listing_html(profile("sistematorg.com"), html)
    assert [lot.external_id for lot in lots] == [
        "sistematorg.com:19431:2", "sistematorg.com:19431:3",
    ]
    assert [lot.cadastral_number for lot in lots] == [
        "12:06:4601002:152", "12:05:0702001:1292",
    ]
    assert all(lot.start_price is None and lot.current_price is None for lot in lots)
    assert all(lot.raw_data["per_lot_detail_verified"] is False for lot in lots)


def test_sistematorg_never_imports_procedure_as_unnumbered_lot():
    html = """
    <table><tr onclick="window.location='/trade_view.php?trade_nid=12345'">
      <td>12345-ОАОФ</td><td>Организатор</td>
      <td>Земельный участок 76:12:0000123:456</td>
    </tr></table>
    """
    assert parse_listing_html(profile("sistematorg.com"), html) == []
    connector = SOURCE_CONNECTOR_TYPES["sistematorg.com"]()
    assert "fetch_lot" in connector.capabilities


@pytest.mark.parametrize(("source_id", "lot_url", "second_lot_url", "next_href", "page_two_url"), [
    (
        "alfalot.ru",
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3A12345",
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3A12346",
        "?LotList.pageNum.lots=1&lotFilterState.activeFilter=SUPCOMING&lotFilterType.activeFilter=SSALE",
        "LotList.pageNum.lots=1",
    ),
    (
        "roseltorg.ru", "https://www.roseltorg.ru/procedure/SAMPLE/12345",
        "https://www.roseltorg.ru/procedure/SAMPLE/12346",
        "?sale=2&status%5B0%5D=1&page=1", "page=1",
    ),
])
def test_evidence_backed_get_pagination_fetches_distinct_second_page(
    source_id, lot_url, second_lot_url, next_href, page_two_url,
):
    first = (
        f"<a href='{lot_url}'>Земельный участок 76:12:0000123:456</a>"
        f"<a href='{next_href}'>2</a>"
    ).encode()
    second = f"<a href='{second_lot_url}'>Квартира 76:12:0000123:457</a>".encode()
    session = FakeSession([
        FakeResponse(200, first), FakeResponse(200, second),
    ])
    connector = SOURCE_CONNECTOR_TYPES[source_id](
        session=session
    )
    page_one = asyncio.run(connector.search({}))
    assert page_one.next_cursor == "2"
    page_two = asyncio.run(connector.search({}, cursor="2"))
    assert page_two.next_cursor is None
    assert page_two_url in session.requested_urls[-1]


@pytest.mark.parametrize(("source_status", "expected"), [
    ("Торги объявлены", "scheduled"),
    ("Извещение опубликовано", "scheduled"),
    ("Прием заявок", "active"),
    ("Приём заявок завершён", "unknown"),
    ("Завершен прием заявок", "unknown"),
    ("Подведение итогов", "unknown"),
    ("Текущая цена 500 000 руб.", "unknown"),
    ("Идут торги", "active"),
    ("Торги завершены", "closed"),
    ("Торги отменены", "closed"),
    ("Торги не состоялись", "closed"),
    ("Торги приостановлены", "paused"),
])
def test_official_status_phrases_do_not_create_false_active_or_closed_lots(source_status, expected):
    from bankrotai.connectors.etp_sources.common import _status
    assert _status(source_status) == expected


def test_nistp_get_form_pagination_uses_actual_provider_method_and_preserves_filters():
    first = """
    <form id='nodesearch' method='get' action='/bankrot/trade_list.php'>
      <input type='hidden' name='pagenum' value='1'>
      <input type='hidden' name='searchtype' value='estate'>
      <input type='checkbox' checked name='region[]' value='76'>
      <input type='checkbox' checked name='region[]' value='77'>
      <input type='checkbox' name='region[]' value='78'>
      <input type='checkbox' checked name='trade_state[]' value='Торги объявлены'>
      <input type='checkbox' checked name='trade_state[]' value='Прием заявок'>
      <input type='checkbox' checked name='trade_state[]' value='Идут торги'>
      <a onclick='pagenum_send(2)'>2</a>
    </form>
    """.encode()
    second = ("<table>" + _nistp_public_lot_row(
        "501501", "71875-ОАОФ", 1, "Земельный участок 77:12:0000123:456",
    ) + "</table>").encode()

    class CaptureGet(FakeSession):
        def __init__(self):
            super().__init__([
                FakeResponse(200, first, "https://nistp.ru/bankrot/trade_list.php"),
                FakeResponse(200, second, "https://nistp.ru/bankrot/trade_list.php"),
            ])
            self.query_params = None

        def get(self, url, *args, **kwargs):
            if "params" in kwargs:
                self.query_params = kwargs["params"]
            return super().get(url, *args, **kwargs)

    session = CaptureGet()
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    result = asyncio.run(connector.search({}, cursor="2"))
    assert [x.external_id for x in result.items] == ["nistp.ru:501501:1"]
    assert session.posts == []
    assert session.query_params is not None
    assert ("pagenum", "2") in session.query_params
    assert ("searchtype", "estate") in session.query_params
    assert [v for k, v in session.query_params if k == "region[]"] == ["76", "77"]
    assert [v for k, v in session.query_params if k == "trade_state[]"] == [
        "Торги объявлены", "Прием заявок", "Идут торги",
    ]


def test_nistp_profile_starts_with_only_active_trade_state_filters():
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(profile("nistp.ru").listing_url).query)
    assert query["trade_state[]"] == [
        "Торги объявлены", "Прием заявок", "Идут торги",
    ]


def test_nistp_rejects_unexpected_form_method():
    first = b"""
    <form id='nodesearch' method='delete' action='/bankrot/trade_list.php'>
      <input name='pagenum' value='1'>
    </form>
    """
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=FakeSession(
        FakeResponse(200, first, "https://nistp.ru/bankrot/trade_list.php")
    ))
    with pytest.raises(SourceSchemaError, match="unsupported pagination form method"):
        asyncio.run(connector.search({}, cursor="2"))


def test_centerr_news_sidebar_does_not_inject_repeated_lots_into_catalogue():
    site = profile("centerr.ru")
    promo = ("https://bankrupt.centerr.ru/public/auctions/lots/view/100/")
    actual = ("https://bankrupt.centerr.ru/public/auctions/lots/view/221/")
    html = f"""
    <div class='news-short'>
      <a href='{promo}'>Важный рекламный лот - земельный участок</a>
    </div>
    <div class='news-body'>
      <a href='{promo}'>Рекламный лот из повторяемого блока</a>
    </div>
    <table><tr><td><a href='{actual}'>Земельный участок 50:01:001:123</a></td></tr></table>
    """
    lots = parse_listing_html(site, html)
    assert [lot.external_id for lot in lots] == ["centerr.ru:auctions-221"]
    assert len(lots) == 1


def test_centerr_non_sidebar_catalogue_links_still_preserved():
    site = profile("centerr.ru")
    html = """
    <table><tr><td>
      <a href='https://bankrupt.centerr.ru/public/auctions/lots/view/221/'>Квартира 1</a>
    </td></tr><tr><td>
      <a href='https://bankrupt.centerr.ru/public/contests/lots/view/222/'>Квартира 2</a>
    </td></tr></table>
    """
    lots = parse_listing_html(site, html)
    assert len(lots) == 2
    assert {lot.external_id for lot in lots} == {
        "centerr.ru:auctions-221", "centerr.ru:contests-222",
    }


def test_nistp_browser_confirmed_get_contract_keeps_all_search_fields():
    """Observed 2026-11-05: a real public page-2 GET used this form shape."""
    from bs4 import BeautifulSoup
    from bankrotai.connectors.etp_sources.pagination import get_form_params

    source_fields = {
        "lot_description": "",
        "trade_number": "",
        "lot_number": "",
        "trade_org": "",
        "arbitr_info": "",
        "debtor_info": "",
        "app_start_from": "",
        "app_start_to": "",
        "app_end_from": "05.12.2026",
        "app_end_to": "",
        "view_type": "trade",
        "pagenum": "1",
    }
    body = "<form id='nodesearch' method='get' action='/bankrot/trade_list.php'>"
    body += "".join(
        f'<input name="{name}" value="{value}">'
        for name, value in source_fields.items()
    )
    body += "</form>"
    form = BeautifulSoup(body, "html.parser").find("form")
    assert form is not None
    params = get_form_params(form, "pagenum", 2)
    assert dict(params) == {**source_fields, "pagenum": "2"}
    assert len(params) == len(source_fields)
    assert ("view_type", "trade") in params
    assert ("app_end_from", "05.12.2026") in params


def test_nistp_real_get_lot_view_does_not_duplicate_search_query_on_page_two():
    """Saved real NISTP HTML: empty GET action, selected view_type=lot."""
    from urllib.parse import parse_qs, urlsplit

    site = profile("nistp.ru")
    assert site.listing_url.startswith("https://nistp.ru/?")
    assert "view_type=lot" in site.listing_url
    first = b"""<html><form id="nodesearch" method="GET" action="">
      <input name="lot_description" value="">
      <input name="app_end_from" value="">
      <input type="radio" name="view_type" value="trade">
      <input type="radio" name="view_type" value="lot" checked>
      <input type="hidden" name="pagenum" value="">
      <a onclick="pagenum_send(2)">2</a>
    </form></html>"""
    second = ("<table>" + _nistp_public_lot_row(
        "502957", "72113-ОАОФ", 4,
        "Машино-место, 14,3 кв. м, кад. № 77:01:0004002:6614",
    ) + _nistp_public_lot_row(
        "502957", "72113-ОАОФ", 5,
        "Машино-место, 13,3 кв. м, кад. № 77:01:0004002:6633",
    ) + "</table>").encode()

    class Captured(FakeSession):
        def __init__(self):
            super().__init__([
                FakeResponse(200, first, site.listing_url),
                FakeResponse(200, second, site.listing_url),
            ])
            self.second_params = None

        def get(self, url, *args, **kwargs):
            if "params" in kwargs:
                self.second_params = kwargs["params"]
            return super().get(url, *args, **kwargs)

    session = Captured()
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    result = asyncio.run(connector.search({}, cursor="2"))
    assert session.requested_urls[0] == site.listing_url
    assert session.requested_urls[1] == "https://nistp.ru/"
    assert session.second_params is not None
    assert [v for k, v in session.second_params if k == "view_type"] == ["lot"]
    assert [v for k, v in session.second_params if k == "pagenum"] == ["2"]
    assert [lot.external_id for lot in result.items] == [
        "nistp.ru:502957:4", "nistp.ru:502957:5",
    ]
    assert [lot.start_price for lot in result.items] == [3274000.0, 3274000.0]
    assert [lot.cadastral_number for lot in result.items] == [
        "77:01:0004002:6614", "77:01:0004002:6633",
    ]


@pytest.mark.parametrize("source_id", ["rus-on.ru", "sistematorg.com"])
def test_saved_legacy_etp_get_forms_replace_previous_query(source_id):
    """Yandex v7 network: both platforms use GET form #tradesearch, not POST."""
    base = profile(source_id).listing_url
    first = b"""<form id="tradesearch" method="GET" action="">
      <input name="trade_number" value="">
      <input type="hidden" name="pagenum" value="">
      <a onclick="pagenum_send(2)">2</a>
    </form>"""

    class CaptureGet(FakeSession):
        def __init__(self):
            super().__init__([
                FakeResponse(200, first, base + "?old=stale"),
                FakeResponse(200, b"page 2", base + "?pagenum=2"),
            ])
            self.params = None

        def get(self, url, *args, **kwargs):
            if "params" in kwargs:
                self.params = kwargs["params"]
            return super().get(url, *args, **kwargs)

    session = CaptureGet()
    connector = SOURCE_CONNECTOR_TYPES[source_id](session=session)
    connector._request(2)
    assert session.posts == []
    assert len(session.requested_urls) == 2
    assert session.requested_urls[1] == base.split("?")[0]
    assert session.params is not None
    assert [value for key, value in session.params if key == "pagenum"] == ["2"]


def test_nistp_trade_only_markup_never_yields_phantom_single_asset():
    """Fail closed: a procedure link is not a lot after switching to lot view."""
    html = """
    <table><tr><td>
      <a href="/bankrot/trade_view.php?trade_nid=502957">72113-ОАОФ</a>
    </td><td>Всего лотов: 10. Машино-место</td></tr></table>
    """
    assert parse_listing_html(profile("nistp.ru"), html) == []


def test_arbbitlot_detail_parses_explicit_rub_fields_without_cross_field_leakage() -> None:
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
    lot = parse_detail_html(
        profile("arbbitlot.ru"),
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


def test_arbbitlot_public_offer_keeps_initial_current_deposit_and_step_distinct() -> None:
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
    lot = parse_detail_html(
        profile("arbbitlot.ru"),
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


def test_arbbitlot_does_not_infer_start_price_from_deposit_or_step() -> None:
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
        SourceSchemaError,
        match="individual detail does not prove an explicit start price",
    ):
        parse_detail_html(
            profile("arbbitlot.ru"),
            html,
            detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/50002/",
        )


def test_arbbitlot_enrichment_fails_closed_without_individual_start_price() -> None:
    from bankrotai.connectors.etp_sources.source_specific import apply_detail
    from bankrotai.domain import NormalizedLot

    def make_lot(*, detail_level: str, start_price):
        return NormalizedLot(
            external_id="arbbitlot.ru:auctions-50002",
            source="arbbitlot",
            source_system="arbbitlot.ru",
            title="Земельный участок",
            description="Земельный участок",
            category="land",
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number="76:01:000000:124",
            vin=None,
            area=None,
            start_price=start_price,
            current_price=None,
            auction_status="active",
            lot_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/50002/",
            source_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/50002/",
            detail_level=detail_level,
            raw_data={"procedure_id": "0037314", "lot_number": "2"},
        )

    listing = make_lot(detail_level="list", start_price=900000)
    detail = make_lot(detail_level="detail", start_price=None)
    with pytest.raises(ValueError, match="arbbitlot_detail_start_price_unverified"):
        apply_detail(listing, detail, version=1)


def test_arbbitlot_multi_lot_catalogue_keeps_per_lot_ids_and_prices() -> None:
    html = """
    <table>
      <tr>
        <th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена</th><th>Статус</th>
      </tr>
      <tr>
        <td>0032715</td><td>Аукцион</td><td>1</td>
        <td><a href="/public/auctions/lots/view/41001/">Автомобиль</a></td>
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
    lots = parse_listing_html(profile("arbbitlot.ru"), html)
    assert [lot.external_id for lot in lots] == [
        "arbbitlot.ru:auctions-41001",
        "arbbitlot.ru:auctions-41002",
        "arbbitlot.ru:auctions-41003",
    ]
    assert len({lot.external_id for lot in lots}) == 3
    assert [lot.start_price for lot in lots] == [450000, 900000, 1500000]
    assert [lot.procedure_number for lot in lots] == ["0032715"] * 3
    assert [lot.raw_data["lot_number"] for lot in lots] == ["1", "2", "3"]


def test_arbbitlot_aspnet_page_two_posts_state_and_reaches_terminal_page() -> None:
    first = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='arb-state'>
      <a href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$Pager$2','')">2</a>
    </form>
    <table><tr><td>
      <a href='/public/auctions/lots/view/41000/'>Zemelniy uchastok 76:01:000000:199</a>
    </td></tr></table>
    """
    second = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='arb-state-2'>
    </form>
    <table>
      <tr><td><a href='/public/auctions/lots/view/41002/'>Garazh 76:01:000000:200</a></td></tr>
      <tr><td><a href='/public/auctions/lots/view/41003/'>Sklad 76:01:000000:201</a></td></tr>
    </table>
    """
    session = FakeSession([
        FakeResponse(200, first, "https://torgi.arbbitlot.ru/public/purchases-all/"),
        FakeResponse(200, second, "https://torgi.arbbitlot.ru/public/purchases-all/"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["arbbitlot.ru"](session=session)
    page1 = asyncio.run(connector.search({}))
    assert page1.next_cursor == "2"
    page2 = asyncio.run(connector.search({}, cursor="2"))
    assert page2.next_cursor is None
    assert [lot.external_id for lot in page2.items] == [
        "arbbitlot.ru:auctions-41002",
        "arbbitlot.ru:auctions-41003",
    ]
    assert session.posts[0][1]["__VIEWSTATE"] == "arb-state"
    assert session.posts[0][1]["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$Pager$2"


def test_arbbitlot_public_offer_catalogue_keeps_per_lot_start_and_current_price() -> None:
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
    lots = parse_listing_html(profile("arbbitlot.ru"), html)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.external_id == "arbbitlot.ru:public-offers-40663"
    assert lot.start_price == 242100
    assert lot.current_price == 169470
    assert lot.auction_status == "active"


def test_arbbitlot_enrichment_preserves_explicit_catalogue_current_price_when_detail_has_no_current_label() -> None:
    from bankrotai.connectors.etp_sources.source_specific import apply_detail

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
    listing = parse_listing_html(profile("arbbitlot.ru"), listing_html)[0]
    detail = parse_detail_html(
        profile("arbbitlot.ru"),
        detail_html,
        detail_url="https://torgi.arbbitlot.ru/public/public-offers/lots/view/40663/",
    )
    assert detail.current_price is None
    enriched = apply_detail(listing, detail, version=1)
    assert enriched.start_price == 242100
    assert enriched.current_price == 169470
    assert enriched.raw_data["detail_start_price_verified"] is True
    assert enriched.raw_data["detail_current_price_verified"] is False
    assert enriched.raw_data["listing_current_price_used_as_fallback"] is True


def test_arbbitlot_pager_double_arrow_advances_beyond_visible_page_group() -> None:
    page_ten = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state-page-10'>
      <a href="javascript:__doPostBack('pager$first','')">1</a>
      <span>10</span>
      <a href="javascript:__doPostBack('pager$nextGroup','')">&gt;&gt;</a>
    </form>
    """
    page_eleven = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state-page-11'>
      <span>11</span>
      <a href="javascript:__doPostBack('pager$page12','')">12</a>
    </form>
    <table><tr><td>
      <a href='/public/auctions/lots/view/51011/'>
        Zemelniy uchastok 76:01:000000:211
      </a>
    </td></tr></table>
    """
    session = FakeSession([
        FakeResponse(
            200,
            page_eleven,
            "https://torgi.arbbitlot.ru/public/purchases-all/",
        ),
    ])
    connector = SOURCE_CONNECTOR_TYPES["arbbitlot.ru"](session=session)
    connector._last_response = FakeResponse(
        200,
        page_ten,
        "https://torgi.arbbitlot.ru/public/purchases-all/",
    )
    connector._last_page = 10

    result = asyncio.run(connector.search({}, cursor="11"))

    assert result.next_cursor == "12"
    assert [lot.external_id for lot in result.items] == [
        "arbbitlot.ru:auctions-51011"
    ]
    assert session.posts[0][1]["__VIEWSTATE"] == "state-page-10"
    assert session.posts[0][1]["__EVENTTARGET"] == "pager$nextGroup"


def test_arbbitlot_organizer_cancelled_status_is_closed_only_for_arbbitlot() -> None:
    listing = """
    <table><tr>
      <td><a href="/public/auctions/lots/view/51012/">
        Avtomobil VIN XTA219110GY223704
      </a></td>
      <td>2 300 000,00</td>
      <td>Отменён организатором</td>
    </tr></table>
    """
    parsed = parse_listing_html(profile("arbbitlot.ru"), listing)
    assert len(parsed) == 1
    assert parsed[0].auction_status == "closed"

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
    lot = parse_detail_html(
        profile("arbbitlot.ru"),
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
def test_arbbitlot_enrichment_rejects_catalogue_detail_identity_mismatch(
    detail_procedure: str,
    detail_lot_number: str,
    expected_error: str,
) -> None:
    from bankrotai.connectors.etp_sources.source_specific import apply_detail

    listing_html = """
    <table>
      <tr>
        <th>№</th><th>Торги</th><th>№ лота</th><th>Лот</th>
        <th>Начальная цена</th><th>Статус</th>
      </tr>
      <tr>
        <td>0032715</td><td>Аукцион</td><td>2</td>
        <td><a href="/public/auctions/lots/view/41002/">
          Гараж 76:01:000000:200
        </a></td>
        <td>900 000,00</td><td>Прием заявок</td>
      </tr>
    </table>
    """
    listing = parse_listing_html(profile("arbbitlot.ru"), listing_html)[0]
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
    detail = parse_detail_html(
        profile("arbbitlot.ru"),
        detail_html,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/41002/",
    )
    with pytest.raises(ValueError, match=expected_error):
        apply_detail(listing, detail, version=1)


def test_arbbitlot_does_not_use_group_arrow_for_missing_normal_next_page() -> None:
    page_three = b"""
    <form action='/public/purchases-all/'>
      <input type='hidden' name='__VIEWSTATE' value='state-page-3'>
      <span>3</span>
      <a href="javascript:__doPostBack('pager$nextGroup','')">&gt;&gt;</a>
    </form>
    """
    connector = SOURCE_CONNECTOR_TYPES["arbbitlot.ru"](session=FakeSession([]))
    connector._last_response = FakeResponse(
        200,
        page_three,
        "https://torgi.arbbitlot.ru/public/purchases-all/",
    )
    connector._last_page = 3

    with pytest.raises(SourceSchemaError, match="page 4 postback target disappeared"):
        asyncio.run(connector.search({}, cursor="4"))
