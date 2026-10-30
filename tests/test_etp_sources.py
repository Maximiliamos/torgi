from __future__ import annotations

import asyncio
import json

import pytest
import requests

from bankrotai.connectors.etp_sources import (
    PROFILES,
    SOURCE_CONNECTOR_TYPES,
    SourceAccessError,
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


def test_duplicate_links_do_not_duplicate_lots() -> None:
    href = "https://rus-on.ru/bankrot/lot_view.php?lot_nid=115011"
    html = f"<div><a href='{href}'>Лот 1</a><a href='{href}'>Земельный участок площадью 792 кв. м.</a></div>"
    lots = parse_listing_html(profile("rus-on.ru"), html)
    assert len(lots) == 1
    assert lots[0].title.startswith("Земельный участок")


def test_clickable_table_row_is_parsed_without_executing_javascript() -> None:
    html = """
    <table><tr onclick="window.location='/trade_view.php?trade_nid=66627'">
      <td>66627-ОАОФ</td><td>Земельный участок площадью 900 кв. м.</td><td>объявлены</td>
    </tr></table>
    """
    lots = parse_listing_html(profile("sistematorg.com"), html)
    assert [lot.external_id for lot in lots] == ["sistematorg.com:66627"]


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


@pytest.mark.parametrize("source_id", ["rts-tender.ru", "etp-profit.ru", "ausib.ru", "utender.ru", "electro-torgi.ru"])
def test_unproven_sources_fail_explicitly(source_id: str) -> None:
    connector = SOURCE_CONNECTOR_TYPES[source_id]()
    with pytest.raises(SourceAccessError):
        asyncio.run(connector.search({}))
    health = asyncio.run(connector.healthcheck())
    assert health.status == "blocked"


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
def test_access_failures_are_not_reported_as_empty_success(status_code: int) -> None:
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=FakeSession(FakeResponse(status_code)))
    with pytest.raises(SourceAccessError, match=str(status_code)):
        asyncio.run(connector.search({}))


def test_timeout_marks_health_as_degraded() -> None:
    connector = SOURCE_CONNECTOR_TYPES["m-ets.ru"](session=FakeSession(requests.Timeout("slow provider")))
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
    listing = f"<tr><td><a href='https://m-ets.ru/243003-1'>Земельный участок</a></td><td>{body}</td></tr>"
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


def test_nistp_multi_lot_trade_preserves_each_property_identity() -> None:
    html = """
    <table><tr><td><a href="/bankrot/trade_view.php?trade_nid=1234">71874-ОАОФ</a></td>
    <td>Управляющий</td>
    <td>Всего лотов: 3 Лот № 1: Земельный участок 76:12:0000123:456
    Лот № 2: Автомобиль BMW
    Лот № 3: Квартира 77:01:0001010:1234</td></tr></table>
    """
    lots = parse_listing_html(profile("nistp.ru"), html)
    assert [lot.external_id for lot in lots] == [
        "nistp.ru:1234:1",
        "nistp.ru:1234:3",
    ]


def test_nistp_parser_does_not_apply_preview_limit_to_full_page() -> None:
    rows = "".join(
        f"""<tr><td><a href='/bankrot/trade_view.php?trade_nid={index}'>
        {70000 + index}-ОАОФ</a></td><td>Управляющий</td>
        <td>Лот № 1: Земельный участок 76:12:0000123:{1000 + index}</td></tr>"""
        for index in range(1, 36)
    )
    lots = parse_listing_html(profile("nistp.ru"), f"<table>{rows}</table>")
    assert len(lots) == 35


def test_shared_trade_card_does_not_claim_unproven_per_lot_detail_fetch() -> None:
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"]()
    assert "fetch_lot" not in connector.capabilities
    with pytest.raises(SourceAccessError, match="per-lot detail isolation"):
        asyncio.run(connector.fetch_lot("nistp.ru:1234:1"))


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
    <form id='nodesearch' action='/bankrot/trade_list.php'>
      <input type='hidden' name='pagenum' value='1'>
      <a onclick='pagenum_send(2)'>2</a>
    </form>
    """
    page_two = b"<a href='/bankrot/trade_view.php?trade_nid=501500'>Lot on page two</a>"
    session = FakeSession([
        FakeResponse(200, page_one, "https://nistp.ru/bankrot/trade_list.php"),
        FakeResponse(200, page_two, "https://nistp.ru/bankrot/trade_list.php"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    result = asyncio.run(connector.search({}, cursor="2"))
    assert result.items[0].external_id == "nistp.ru:501500"
    assert session.posts[0][1]["pagenum"] == "2"


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
