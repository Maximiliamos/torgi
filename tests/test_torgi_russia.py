import asyncio
import json

from bankrotai.connectors.registry.torgi_russia import TorgiRussiaConnector
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import TorgiRussiaClient


def _nextjs_search_html(items: list[dict], *, next_page: int | None = None) -> str:
    flight = "27:" + json.dumps(
        ["$", "$L95", None, {"lots": items}],
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    script_payload = json.dumps([1, flight], ensure_ascii=False)
    links = "".join(f'<a href="/lot/{item["id"]}">lot</a>' for item in items)
    if next_page is not None:
        links += f'<a href="/search?search=&categorie_childs%5B%5D=6&page={next_page}">{next_page}</a>'
    return f"<html><body><main>{links}</main><script>self.__next_f.push({script_payload})</script></body></html>"


def _new_site_lot(*, lot_id: int = 7180653, region_id: int = 33) -> dict:
    return {
        "id": lot_id,
        "title": "Здание 33:01:000001:42",
        "status": {"id": 1, "title": "Идёт приём заявок"},
        "pictures": [
            {
                "id": 1,
                "link": "https://example.test/full.jpg",
                "thumb_link": "https://example.test/thumb.jpg",
                "title": "photo.jpg",
            }
        ],
        "start_price": 5105700,
        "current_price": 4900000,
        "region": {"id": region_id, "title": "Владимирская область"},
        "marketplace": {"id": 50, "title": "ЭТП.ТР"},
        "trade_type": {"id": 5, "title": "Коммерческие торги"},
        "trade_form": "Открытый аукцион",
        "trade_link": "https://example.test/trade/1",
        "begin_offer_time": "2026-10-08 11:00:00",
        "category_ids": [6, 343],
    }


def test_torgi_russia_search_requires_matching_cadastral_number() -> None:
    html = """
    <article><a href="/lot/111">First</a><p>76:02:000000:1</p></article>
    <article><a href="/lot/6884641">Second</a><p>76:02:071501:198</p></article>
    """

    assert TorgiRussiaClient._matching_lot_url(html, "76:02:071501:198").endswith("/lot/6884641")
    assert TorgiRussiaClient._matching_lot_url(html, "76:02:071501:999") is None


def test_torgi_russia_lot_page_parses_gallery_and_related_links() -> None:
    html = """
    <div id="lot-gallery" data-gallery='[
      {"url":"/pictures/one.jpg","thumb_url":"/thumb/one.jpg"},
      {"url":"https://cdn.example/two.jpg"}
    ]'></div>
    <a href="https://catalog.lot-online.ru/notice/21000002210000009602/1">Торги на ЭТП</a>
    <a href="https://torgi.gov.ru/new/public/lots/lot/example/(lotInfo:info)">Лот на ГИС Торги</a>
    """

    result = TorgiRussiaClient.parse_lot_page(
        html,
        "https://xn----etbpba5admdlad.xn--p1ai/lot/6884641",
    )

    assert result.torgi_russia_url.endswith("/lot/6884641")
    assert result.etp_url == "https://catalog.lot-online.ru/notice/21000002210000009602/1"
    assert result.gis_torgi_url.endswith("/(lotInfo:info)")
    assert result.image_urls == [
        "https://xn----etbpba5admdlad.xn--p1ai/pictures/one.jpg",
        "https://cdn.example/two.jpg",
    ]


def test_torgi_russia_lot_page_parses_structured_detail_fields() -> None:
    html = """
    <dl>
      <dt>Адрес местонахождения имущества</dt><dd>г. Ярославль, ул. Свободы, 1</dd>
      <dt>Категория имущества</dt><dd>Земельные участки</dd>
      <dt>Начало приема заявок</dt><dd>20.08.2026 в 09:00</dd>
      <dt>Окончание приема заявок</dt><dd>25.08.2026 в 18:00</dd>
      <dt>Дата проведения аукциона</dt><dd>27.08.2026 в 10:30</dd>
    </dl>
    """

    result = TorgiRussiaClient.parse_lot_page(html, "https://торги-россии.рф/lot/1")

    assert result.address == "г. Ярославль, ул. Свободы, 1"
    assert result.category == "Земельные участки"
    assert result.application_start_at.isoformat() == "2026-08-20T09:00:00"
    assert result.application_deadline.isoformat() == "2026-08-25T18:00:00"
    assert result.auction_at.isoformat() == "2026-08-27T10:30:00"


def test_torgi_russia_lot_page_parses_current_lot_data_rows() -> None:
    html = """
    <div class="lot-data__text"><span>Начало приёма заявок:</span> 24.08.2026 10:00</div>
    <div class="lot-data__text"><span>Конец приёма заявок:</span> 28.09.2026 10:00</div>
    <div class="lot-data__text"><span>Начало приема ценовых предложений:</span> 30.09.2026 10:00</div>
    <div class="lot-data__text"><span>Конец приема ценовых предложений:</span> 30.09.2026 15:00</div>
    """

    result = TorgiRussiaClient.parse_lot_page(html, "https://торги-россии.рф/lot/2")

    assert result.application_start_at.isoformat() == "2026-08-24T10:00:00"
    assert result.application_deadline.isoformat() == "2026-09-28T10:00:00"
    assert result.auction_at.isoformat() == "2026-09-30T15:00:00"


def test_torgi_russia_parse_search_page() -> None:
    html = """
    <main><article class="card">
      <div class="card-meta"><div class="card-meta__item">7143576</div><div class="card-meta__item">Республика Башкортостан</div></div>
      <div class="card-gallery" data-photos='[{"url":"/pictures/one.png"}]'></div>
      <h3 class="card__title"><a href="/lot/7143576">Земельный участок 02:31:040801:78</a></h3>
      <p class="card__excerpt">Участок площадью 1489 кв.м.</p>
      <div class="card__bids" data-current-bid="345 870,00" data-start-bid="500 000,00"></div>
    </article></main>
    """
    lots = TorgiRussiaClient.parse_search_page(
        html,
        "https://xn----etbpba5admdlad.xn--p1ai/search?categories%5B0%5D=6&history_only=0",
    )
    assert len(lots) == 1
    lot = lots[0]
    assert lot.external_id == "torgi-russia:7143576"
    assert lot.region_slug == "02"
    assert lot.cadastral_number == "02:31:040801:78"
    assert lot.start_price == 500000
    assert lot.current_price == 345870
    assert lot.raw_data["image_urls"] == [
        "https://xn----etbpba5admdlad.xn--p1ai/pictures/one.png"
    ]


def test_torgi_russia_parse_new_search_payload() -> None:
    payload = {"data": [_new_site_lot()]}
    lots = TorgiRussiaClient.parse_search_payload(payload)
    assert len(lots) == 1
    assert lots[0].external_id == "torgi-russia:7180653"
    assert lots[0].region_slug == "33"
    assert lots[0].cadastral_number == "33:01:000001:42"
    assert lots[0].start_price == 5105700
    assert lots[0].current_price == 4900000
    assert lots[0].raw_data["image_urls"] == ["https://example.test/full.jpg"]
    assert lots[0].raw_data["marketplace"]["id"] == 50


def test_torgi_russia_extracts_lots_from_nextjs_flight_payload() -> None:
    html = _nextjs_search_html([_new_site_lot()], next_page=2)
    records, metadata = TorgiRussiaClient.parse_next_search_payload(
        html,
        page_url=(
            "https://xn----etbpba5admdlad.xn--p1ai/"
            "search?search=&categorie_childs%5B%5D=6&page=1"
        ),
        current_page=1,
    )

    assert [item["id"] for item in records] == [7180653]
    assert records[0]["trade_link"] == "https://example.test/trade/1"
    assert metadata["has_more"] is True
    assert metadata["total_pages"] == 2


def test_torgi_russia_nextjs_parser_ignores_unlinked_widget_lots() -> None:
    linked = _new_site_lot(lot_id=7180653)
    hidden = _new_site_lot(lot_id=7180654)
    flight = "27:" + json.dumps(
        ["$", "$L95", None, {"lots": [linked, hidden]}],
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    html = (
        '<main><a href="/lot/7180653">shown</a></main>'
        f"<script>self.__next_f.push({json.dumps([1, flight], ensure_ascii=False)})</script>"
    )

    records, _ = TorgiRussiaClient.parse_next_search_payload(
        html,
        page_url="https://xn----etbpba5admdlad.xn--p1ai/search?page=1",
        current_page=1,
    )

    assert [item["id"] for item in records] == [7180653]


def test_torgi_russia_new_payload_matches_cadastral_number() -> None:
    payload = {
        "data": [
            {"id": 1, "title": "Лот 76:02:000000:1"},
            {"id": 2, "title": "Лот 76:02:071501:198"},
        ]
    }
    result = TorgiRussiaClient._matching_lot_url_from_payload(payload, "76:02:071501:198")
    assert result == "https://xn----etbpba5admdlad.xn--p1ai/lot/2"


def test_torgi_russia_parse_detail_payload() -> None:
    detail = TorgiRussiaClient.parse_detail_payload(
        {
            "information": (
                "Недвижимое имущество, расположенное по адресу: Владимирская область, "
                "г. Суздаль, ул. Ленина, д. 1. К\\н: 33:05:130102:857<br>Начальная цена: 10 ₽"
            ),
            "cadastrals": ["33:05:130102:857"],
            "pictures": [{"link": "https://example.test/one.jpg"}],
            "trade_link": "https://example.test/trade/1",
            "status": {"id": 1, "title": "Идёт приём заявок"},
        }
    )

    assert detail["address"] == "Владимирская область, г. Суздаль, ул. Ленина, д. 1"
    assert detail["cadastral_numbers"] == ["33:05:130102:857"]
    assert detail["image_urls"] == ["https://example.test/one.jpg"]
    assert "Начальная цена" in detail["description"]


def test_torgi_russia_search_uses_public_nextjs_page_not_legacy_api() -> None:
    item = _new_site_lot()
    html = _nextjs_search_html([item])

    class Response:
        url = (
            "https://xn----etbpba5admdlad.xn--p1ai/"
            "search?search=&categorie_childs%5B%5D=6&page=1"
        )
        text = html

        def raise_for_status(self):
            return None

    class Session:
        headers = {}
        called_url = None
        called_params = None

        def get(self, url, *, params=None, timeout=None):
            self.called_url = url
            self.called_params = params
            return Response()

    session = Session()
    client = TorgiRussiaClient(session=session)
    lots, metadata = client.search_lots(TorgiRussiaSearchFilters())

    assert session.called_url == "https://xn----etbpba5admdlad.xn--p1ai/search"
    assert ("categorie_childs[]", 6) in session.called_params
    assert ("page", 1) in session.called_params
    assert len(lots) == 1
    assert metadata["transport"] == "public-nextjs-html"
    assert lots[0].raw_data["transport"] == "public-nextjs-html"


def test_torgi_russia_connector_pages_public_catalog_without_region_api() -> None:
    connector = TorgiRussiaConnector()
    calls = []

    def search(filters):
        calls.append((filters.region_id, filters.page))
        return [], {
            "has_more": filters.page == 1,
            "total_pages": 2,
        }

    connector.client.search_lots = search
    first = asyncio.run(connector.search(TorgiRussiaSearchFilters()))
    second = asyncio.run(connector.search(TorgiRussiaSearchFilters(), first.next_cursor))

    assert calls == [(None, 1), (None, 2)]
    assert first.next_cursor == "2"
    assert second.next_cursor is None
    assert second.metadata["progress_total"] == 2


def test_torgi_russia_old_region_cursor_restarts_public_catalog() -> None:
    assert TorgiRussiaConnector._decode_cursor("region:5:17") == 1
