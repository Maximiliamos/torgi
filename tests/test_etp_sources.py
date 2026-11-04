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
from bankrotai.connectors.etp_sources.common import _page_url


def profile(source_id: str):
    return next(item for item in PROFILES if item.source_id == source_id)


@pytest.mark.parametrize(
    ("source_id", "href", "title"),
    [
        ("m-ets.ru", "https://m-ets.ru/243003-1", "Жилой дом 78 кв.м., кадастровый номер 50:01:001:123, 4 500 000 руб."),
        ("arbbitlot.ru", "https://torgi.arbbitlot.ru/public/auctions/lots/view/44914/", "Автомобиль LADA GRANTA, VIN XTA219110GY223704, 350 000 руб."),
        ("zakazrf.ru", "https://bankrot.zakazrf.ru/public/public-offers/lots/view/18797/", "Стояночное место 12,5 кв. м, кадастровый номер 16:50:010326:323"),
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


def test_cdtrf_catalogue_covers_every_requested_active_property_stage() -> None:
    url = profile("cdtrf.ru").listing_url
    for value in (
        "Declare=true", "RecieveReq=true", "DefinePart=true", "TradeGo=true",
        "Categories=31%2C32%2C33%2C34%2C21%2C22%2C23", "PageNum=1",
    ):
        assert value in url


def test_roseltorg_second_page_is_not_rewritten_to_first_page() -> None:
    assert _page_url(profile("roseltorg.ru"), 2).endswith("&page=2")


def test_registry_uses_new_parsers_except_for_mature_source_specific_connectors() -> None:
    preserved = {"m-ets.ru", "cdtrf.ru", "rts-tender.ru"}
    for source_id, connector_cls in SOURCE_CONNECTOR_TYPES.items():
        if source_id not in preserved:
            assert isinstance(connector_registry.create(source_id), connector_cls)


@pytest.mark.parametrize("source_id", ["rts-tender.ru", "utender.ru"])
def test_unproven_sources_fail_explicitly(source_id: str) -> None:
    connector = SOURCE_CONNECTOR_TYPES[source_id]()
    with pytest.raises(SourceAccessError):
        asyncio.run(connector.search({}))
    health = asyncio.run(connector.healthcheck())
    assert health.status == "blocked"


def test_electro_torgi_live_card_contract_has_price_and_pagination() -> None:
    html = """
    <ul><li class="collection-item"><div class="row">
      <div><a href="/oaof/43054">Торги по реализации имущества должника</a>
      <p>Номер торгов: <a href="/oaof/43054">43054-ОАОФ</a> Объявлен</p>
      <p>Лот 1: <span>Гараж, кадастровый номер 50:56:0000000:4317, площадь 19,7 кв.м.</span></p></div>
      <div><div>1 лот на сумму</div><div>883 000.00 <span>₽</span></div></div>
    </div></li></ul>
    <ul class="pagination"><li><a href="/lots?page=2">2</a></li></ul>
    """
    profile = next(p for p in PROFILES if p.source_id == "electro-torgi.ru")
    lots = parse_listing_html(profile, html)
    assert profile.blocked_reason is None
    assert profile.supports_get_pagination is True
    assert len(lots) == 1
    assert lots[0].external_id == "electro-torgi.ru:oaof-43054:1"
    assert lots[0].start_price == 883_000
    assert lots[0].cadastral_number == "50:56:0000000:4317"


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
      <td>Лот № 1: Нежилое помещение 27.8 кв.м., 27:23:0050709:453</td>
      <td>объявлены</td><td>03.11.2026 07:00</td>
    </tr></table><a href="?page=2">2</a>
    """
    item = parse_listing_html(profile("ausib.ru"), listing)[0]
    assert item.external_id == "ausib.ru:104685887:1"
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


@pytest.mark.parametrize(
    ("source_id", "detail_url", "external_id", "description", "price", "cadastre"),
    [
        (
            "aukcioncenter.ru",
            "https://aukcioncenter.ru/trade/view/purchase/general.html?id=103556972",
            "aukcioncenter.ru:103556972:1",
            "Земельный участок и жилой дом, кадастровые номера "
            "24:04:0301008:586 и 24:04:0301008:1576, Красноярский край",
            "7 200 000,00 руб",
            "24:04:0301008:586",
        ),
        (
            "ausib.ru",
            "https://ausib.ru/trade/view/purchase/general.html?id=104687293",
            "ausib.ru:104687293:1",
            "Земельный участок, кадастровый номер 66:59:0217004:636",
            "370 000,00 руб",
            "66:59:0217004:636",
        ),
    ],
)
def test_live_procedure_detail_price_contracts(
    source_id: str,
    detail_url: str,
    external_id: str,
    description: str,
    price: str,
    cadastre: str,
) -> None:
    html = f"""
    <html><body><table id="lotNumber1">
      <tr><td>Предмет торгов</td><td>{description}</td></tr>
      <tr><td>Начальная цена продажи имущества</td><td>{price}</td></tr>
      <tr><td>Статус торгов</td><td>Торги объявлены</td></tr>
    </table>Подробная публичная карточка процедуры и условия продажи имущества.
    </body></html>
    """
    parsed = parse_detail_html(
        profile(source_id), html, detail_url=detail_url, external_id=external_id,
    )
    assert parsed.start_price == (7_200_000 if source_id == "aukcioncenter.ru" else 370_000)
    assert parsed.cadastral_number == cadastre
    assert parsed.auction_status == "scheduled"


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
        self.get_params: list[dict[str, str] | None] = []
        self.posts: list[tuple[str, dict[str, str]]] = []

    def _next(self):
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response

    def get(self, url, *_args, **kwargs):
        self.requested_urls.append(url)
        self.get_params.append(kwargs.get("params"))
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


def test_ausib_same_url_302_is_explicitly_detected_without_following() -> None:
    url = "https://ausib.ru/etp/trade/list.html"
    response = FakeResponse(302, b"", url)
    response.headers = {"Location": url}
    connector = SOURCE_CONNECTOR_TYPES["ausib.ru"](session=FakeSession(response))
    with pytest.raises(SourceAccessError, match=r"self-redirect loop \\(HTTP 302\\)"):
        asyncio.run(connector.search({}))
    assert connector.session.requested_urls == [url]


def test_provider_cross_host_redirect_remains_refused() -> None:
    url = "https://ausib.ru/etp/trade/list.html"
    response = FakeResponse(302, b"", url)
    response.headers = {"Location": "https://untrusted.example/"}
    connector = SOURCE_CONNECTOR_TYPES["ausib.ru"](session=FakeSession(response))
    with pytest.raises(SourceAccessError, match="provider redirect was refused"):
        asyncio.run(connector.search({}))
    assert connector.session.requested_urls == [url]


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


def test_detail_label_value_cells_provide_price_address_and_status() -> None:
    html = """
    <html><body><table>
      <tr><td class="tdTitle">Наименование:</td><td class="tdContent">
        Квартира по адресу: г. Москва, ул. Лесная, д. 5, кв. 2,
        кадастровый номер: 77:01:0001001:1234
      </td><td class="tdTitle">Статус:</td><td class="tdContent">Извещение опубликовано</td></tr>
      <tr><td></td><td></td><td class="tdTitle">Начальная цена, руб.:</td>
      <td class="tdContent">6 651 000,00</td></tr>
    </table>Подробные открытые сведения о продаже имущества и условиях участия.</body></html>
    """
    lot = parse_detail_html(
        profile("arbbitlot.ru"), html,
        detail_url="https://torgi.arbbitlot.ru/public/auctions/lots/view/224/",
    )
    assert lot.title.startswith("Квартира по адресу")
    assert lot.address == "г. Москва, ул. Лесная, д. 5, кв. 2"
    assert lot.start_price == 6_651_000
    assert lot.auction_status == "scheduled"


def test_roseltorg_semantic_location_is_lot_address_not_footer_address() -> None:
    html = """
    <html><body>
      <h1>квартира, площадью 67,8 кв.м., этаж 3</h1>
      <div>Прием заявок</div><div>Начальная цена 7 300 000,00 ₽</div>
      <div class="lot-location">
        <span>Расположение</span>
        <span>350004, Краснодарский край, г Краснодар, ул Минская, д 121, кв 9</span>
        <span>© Яндекс</span><a>Условия использования</a><span>Как добраться</span>
      </div>
      <footer>Адрес 119435, город Москва, Большой Саввинский переулок, дом 12</footer>
      <p>Открытая публичная карточка продажи недвижимого имущества.</p>
    </body></html>
    """
    lot = parse_detail_html(
        profile("roseltorg.ru"), html,
        detail_url="https://www.roseltorg.ru/procedure/COM04112600002/1",
    )
    assert lot.address == (
        "350004, Краснодарский край, г Краснодар, ул Минская, д 121, кв 9"
    )
    assert lot.start_price == 7_300_000
    assert lot.auction_status == "active"


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


def test_sberbank_uses_precise_geo_address_and_published_status() -> None:
    from bankrotai.connectors.etp_sources import parse_sberbank_json

    payload = {"PurchaseList": [{"_source": {
        "objectHref": "https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/7/0/0/56230",
        "title": "Квартира с кадастровым номером 23:01:0101001:12",
        "purchAmountRUB": "4792752.96",
        "PurchaseStageTerm": "Опубликована",
        "Address": "Краснодарский край",
        "GeoDataAddress": "Краснодарский край, г. Лабинск, ул. Пархоменко, д. 1",
    }}]}
    lot = parse_sberbank_json(profile("sberbank-ast.ru"), payload, page=1)[0]
    assert lot.address == "Краснодарский край, г. Лабинск, ул. Пархоменко, д. 1"
    assert lot.auction_status == "scheduled"
    assert lot.start_price == 4_792_752.96


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


def test_form_pagination_posts_page_number_when_provider_explicitly_uses_post() -> None:
    page_one = b"""
    <form id='nodesearch' method='POST' action='/bankrot/trade_list.php'>
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


def test_nistp_real_get_form_pagination_preserves_filters_and_page_number() -> None:
    # Live Home evidence, 2026-11-04: NISTP nodesearch is method=GET.
    # Sending its requested second page via POST returned HTTP 200 but no
    # matching lots, while page 1 contained 33 lot candidates.
    first = b"""
    <form id='nodesearch' method='GET' action='/bankrot/trade_list.php'>
      <input type='hidden' name='pagenum' value='1'>
      <input type='hidden' name='view_type' value='trade'>
      <input name='lot_description' value='land'>
      <a onclick='pagenum_send(2)'>2</a>
    </form>
    """
    second = b"<a href='/bankrot/trade_view.php?trade_nid=601501'>Lot 2</a>"
    session = FakeSession([
        FakeResponse(200, first, "https://nistp.ru/bankrot/trade_list.php"),
        FakeResponse(200, second, "https://nistp.ru/bankrot/trade_list.php"),
    ])
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](session=session)
    page = asyncio.run(connector.search({}, cursor="2"))
    assert page.items[0].external_id == "nistp.ru:601501"
    assert session.posts == []
    assert session.get_params[0] is None
    assert session.get_params[1] == {
        "pagenum": "2", "view_type": "trade", "lot_description": "land",
    }
    assert session.requested_urls[1] == "https://nistp.ru/bankrot/trade_list.php"


def test_nistp_form_pagination_rejects_unknown_http_method() -> None:
    first = b"""
    <form id='nodesearch' method='TRACE' action='/bankrot/trade_list.php'>
      <input name='pagenum' value='1'>
    </form>
    """
    connector = SOURCE_CONNECTOR_TYPES["nistp.ru"](
        session=FakeSession(
            FakeResponse(200, first, "https://nistp.ru/bankrot/trade_list.php")
        )
    )
    with pytest.raises(SourceSchemaError, match="unsupported pagination form method"):
        asyncio.run(connector.search({}, cursor="2"))


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


def test_sistematorg_live_single_lot_price_contract() -> None:
    html = """
    <html><body><h1>Торги 20375-ОАОФ</h1>
    Статус Торги объявлены. Всего лотов: 1. Лот № 1: Земельный участок.
    Начальная цена 93 240.00 руб. Официальная карточка процедуры содержит
    сроки приема заявок, сведения о должнике и подробное описание имущества.
    </body></html>
    """
    lot = parse_detail_html(
        profile("sistematorg.com"), html,
        detail_url="https://sistematorg.com/trade_view.php?trade_nid=66788",
    )
    assert lot.start_price == 93_240
    assert lot.auction_status == "scheduled"


def test_alfalot_upcoming_receivables_are_classified_outside_property() -> None:
    html = """
    <table>
      <tr><td><a href="/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q1i4mp4hev55ju4">
        0020626009DS.lot1 Право требования задолженности</a></td>
        <td>45 133 863,54 руб.</td></tr>
      <tr><td><a href="/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q22pcibehbiuhnk">
        0020626011DS.lot1 Дебиторская задолженность</a></td>
        <td>5 024 151,34 руб.</td></tr>
    </table>
    """
    lots = parse_listing_html(profile("alfalot.ru"), html)
    assert [lot.category for lot in lots] == ["receivables", "receivables"]
    assert all(lot.category not in {"real_estate", "land"} for lot in lots)


@pytest.mark.parametrize(
    ("source_id", "href", "prefix"),
    [
        ("aukcioncenter.ru", "/trade/view/purchase/general.html?id=5512", "5512"),
        ("ausib.ru", "/trade/view/purchase/general.html?id=17312", "17312"),
    ],
)
def test_procedure_cards_preserve_each_property_lot_identity(
    source_id: str, href: str, prefix: str,
) -> None:
    html = f"""
    <table><tr><td><a href='{href}'>Торги</a></td><td>
      Лот № 1: Земельный участок 76:12:0000123:456. Начальная цена 900 000 руб.
      Лот № 2: Автомобиль BMW. Начальная цена 700 000 руб.
      Лот № 3: Квартира 77:01:0001010:1234. Начальная цена 5 200 000 руб.
    </td></tr></table>
    """
    lots = parse_listing_html(profile(source_id), html)
    assert [lot.external_id for lot in lots] == [
        f"{source_id}:{prefix}:1", f"{source_id}:{prefix}:3",
    ]
    assert [lot.start_price for lot in lots] == [900_000, 5_200_000]
    assert all(lot.raw_data["per_lot_detail_verified"] is False for lot in lots)


def test_fabrikant_next_card_uses_stable_provider_lot_id() -> None:
    html = """
    <div data-id="679967052">
      <span>Аукцион № 5593095-2</span>
      <a href="https://fabrikant.ru/v2/trades/procedure/view/6KtDsKIF2zfYwTnYd1XXDg">
        Квартира 58,2 м2, кадастровый номер 21:01:000000:13293
      </a>
      <div>2 980 125,00 RUB</div><div>начальная цена</div>
      <span>Ожидается начало приема заявок</span><span>Торги по банкротству</span>
    </div>
    """
    lot = parse_listing_html(profile("fabrikant.ru"), html)[0]
    assert lot.external_id == "fabrikant.ru:679967052"
    assert lot.raw_data["procedure_number"] == "5593095"
    assert lot.raw_data["lot_number"] == "2"
    assert lot.start_price == 2_980_125


def test_fabrikant_react_flight_card_uses_stable_provider_lot_id() -> None:
    flight = (
        '"data-id":679967052,'
        '"children":"Ожидается начало приема заявок",'
        '"url":"https://fabrikant.ru/v2/trades/procedure/view/6KtDsKIF2zfYwTnYd1XXDg",'
        '"name":"Квартира 58,2 м2, кадастровый номер 21:01:000000:13293",'
        '"lotId":679967052,"procedureNumber":5593095,"sequenceNumber":2,'
        '"font-heading text-base font-bold","children":["2 980 125"'
    )
    html = f"<script>self.__next_f.push({json.dumps([1, flight], ensure_ascii=False)})</script>"

    lot = parse_listing_html(profile("fabrikant.ru"), html)[0]

    assert lot.external_id == "fabrikant.ru:679967052"
    assert lot.raw_data["next_flight_evidence"] is True
    assert lot.raw_data["procedure_number"] == "5593095"
    assert lot.raw_data["lot_number"] == "2"
    assert lot.start_price == 2_980_125
    assert lot.auction_status == "scheduled"


def test_electro_torgi_procedure_card_is_split_without_using_procedure_total() -> None:
    html = """
    <div class='trade-card'><a href='/oaof/43054'>43054-ОАОФ</a>
      Идет прием заявок.
      Лот 1: Гараж, кадастровый номер 50:56:0000000:4317.
      Лот 2: Квартира, кадастровый номер 50:12:0100803:6769.
      2 лота на сумму 11 446 000.00 ₽
    </div>
    """
    lots = parse_listing_html(profile("electro-torgi.ru"), html)
    assert [lot.external_id for lot in lots] == [
        "electro-torgi.ru:oaof-43054:1",
        "electro-torgi.ru:oaof-43054:2",
    ]
    assert all(lot.start_price is None for lot in lots)
    assert all(lot.auction_status == "active" for lot in lots)


def test_legacy_procedure_table_keeps_rows_isolated_and_infers_single_lot() -> None:
    html = """
    <div class='page-with-pagination'>
      <table><tbody>
        <tr onclick="window.location='/trade/view/purchase/general.html?id=104687084'">
          <td>17326-OTPP</td>
          <td>Земельный участок, кадастровый номер 66:12:5203007:170.</td>
          <td>объявлены</td>
        </tr>
        <tr onclick="window.location='/trade/view/purchase/general.html?id=104684153'">
          <td>17312-OAOF</td>
          <td>Лот №1: Земельный участок, кадастровый номер 17:05:1702001:3050.
              Лот №2: Квартира, кадастровый номер 17:18:0105048:2740.</td>
          <td>идёт приём заявок</td>
        </tr>
      </tbody></table>
      <a href='/etp/trade/list.html?page=2'>2</a>
    </div>
    """

    lots = parse_listing_html(profile("ausib.ru"), html)

    assert [lot.external_id for lot in lots] == [
        "ausib.ru:104687084:1",
        "ausib.ru:104684153:1",
        "ausib.ru:104684153:2",
    ]
    assert [lot.cadastral_number for lot in lots] == [
        "66:12:5203007:170",
        "17:05:1702001:3050",
        "17:18:0105048:2740",
    ]


def test_mets_card_uses_price_before_caption_not_later_deposit() -> None:
    html = """
    <a href='/243750-1' class='search-comp-item'>
      <div class='cost'><span>300 000 000</span><span> ₽</span></div>
      <div class='title'>Начальная цена</div>
      <div>Жилой дом, кадастровый номер 50:08:0050435:1021.</div>
      <div>Размер задатка 15 000 000 ₽</div>
    </a>
    """
    lot = parse_listing_html(profile("m-ets.ru"), html)[0]
    assert lot.start_price == 300_000_000


def test_mets_link_card_does_not_mix_adjacent_card_price_or_lot_number() -> None:
    html = """
    <div class='search-comp-list card-result-grid'>
      <a href='/243750-1' class='card--shadow search-comp-item'>
        <div>243750-МЭТС, Лот №1</div>
        <div class='price current'>300 000 000 ₽</div>
        <div class='subtext'>Начальная цена</div>
        <div>Жилой дом, кадастровый номер 50:08:0050435:1021.</div>
      </a>
      <a href='/243749-1' class='card--shadow search-comp-item'>
        <div>243749-МЭТС, Лот №1</div>
        <div class='price current'>1 479 500 ₽</div>
        <div class='subtext'>Начальная цена</div>
        <div>Земельный участок, кадастровый номер 65:01:0201003:71.</div>
      </a>
    </div>
    """

    lots = parse_listing_html(profile("m-ets.ru"), html)

    assert [lot.start_price for lot in lots] == [300_000_000, 1_479_500]
    assert [lot.cadastral_number for lot in lots] == [
        "50:08:0050435:1021",
        "65:01:0201003:71",
    ]


def test_centerr_ignores_editorial_news_link_that_looks_like_a_lot() -> None:
    html = """
    <div class='news-short'><div class='news-body'>
      Новость об отеле с кадастровым номером 23:49:0302012:1938.
      <a href='/public/public-offers/lots/view/1170257/'>Ссылка на торги</a>
    </div></div>
    <table class='gridTable'><tr class='gridRow'>
      <td>1</td>
      <td>Квартира, кадастровый номер 50:16:0502052:582.</td>
      <td><a href='/public/auctions/lots/view/1171626/'>Открыть лот</a></td>
    </tr></table>
    """

    lots = parse_listing_html(profile("centerr.ru"), html)

    assert [lot.external_id for lot in lots] == ["centerr.ru:auctions-1171626"]
    assert lots[0].cadastral_number == "50:16:0502052:582"


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
    assert "fetch_lot" not in connector.capabilities
    with pytest.raises(SourceAccessError, match="per-lot detail isolation"):
        asyncio.run(connector.fetch_lot("sistematorg.com:12345:1"))


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
            "?sale=2&status%5B0%5D=1&page=2", "page=2",
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
