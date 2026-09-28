import asyncio
import json

from bankrotai.connectors.registry.torgi_russia import TorgiRussiaConnector
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import PUBLIC_REGION_FILTER_EXPECTATIONS, PUBLIC_REGION_FILTER_IDS, TorgiRussiaClient


def _nextjs_search_html(
    items: list[dict],
    *,
    current_page: int = 1,
    last_page: int = 1,
    total: int | None = None,
    per_page: int = 24,
) -> str:
    flight = "25:" + json.dumps(
        [
            "$",
            "$L86",
            None,
            {
                "initialLots": items,
                "initialMeta": {
                    "current_page": current_page,
                    "last_page": last_page,
                    "per_page": per_page,
                    "total": len(items) if total is None else total,
                },
            },
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    script_payload = json.dumps([1, flight], ensure_ascii=False)
    links = "".join(f'<a href="/lot/{item["id"]}">lot</a>' for item in items)
    return (
        "<html><body><main>"
        + links
        + f"</main><script>self.__next_f.push({script_payload})</script></body></html>"
    )


def _new_site_lot(*, lot_id: int = 7180653, region_title: str = "Владимирская область") -> dict:
    return {
        "id": lot_id,
        "title": "Здание 33:01:000001:42",
        "status": "Идёт приём заявок",
        "status_id": 1,
        "pictures": [
            {
                "id": 1,
                "url": "https://example.test/full.jpg",
            }
        ],
        "start_price": 5105700,
        "current_price": 4900000,
        "price_difference": 205700,
        "region_title": region_title,
        "marketplace_title": "ЭТП.ТР",
        "trade_type_title": "Коммерческие торги",
        "trade_form": "Открытый аукцион",
        "days_remaining": "10 дней до торгов",
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
    assert lots[0].raw_data["marketplace"] == "ЭТП.ТР"
    assert lots[0].raw_data["trade_type"] == "Коммерческие торги"
    assert lots[0].raw_data["source_status"] == "Идёт приём заявок"


def test_torgi_russia_extracts_lots_from_nextjs_flight_payload() -> None:
    html = _nextjs_search_html([_new_site_lot()], current_page=1, last_page=209, total=5000)
    records, metadata = TorgiRussiaClient.parse_next_search_payload(
        html,
        page_url=(
            "https://xn----etbpba5admdlad.xn--p1ai/"
            "search?search=&categorie_childs%5B%5D=6&page=1"
        ),
        current_page=1,
    )

    assert [item["id"] for item in records] == [7180653]
    assert records[0]["region_title"] == "Владимирская область"
    assert metadata["has_more"] is True
    assert metadata["total_pages"] == 209
    assert metadata["total"] == 5000
    assert metadata["per_page"] == 24


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


def test_torgi_russia_search_uses_verified_real_estate_categories() -> None:
    item = _new_site_lot()
    html = _nextjs_search_html([item], current_page=1, last_page=1, total=1)

    class Response:
        url = (
            "https://xn----etbpba5admdlad.xn--p1ai/"
            "search?categorie_childs%5B%5D=7&page=1"
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
    selected_categories = [
        value for key, value in session.called_params if key == "categorie_childs[]"
    ]
    assert selected_categories == [7, 33, 343, 9, 18, 17, 16]
    assert ("page", 1) in session.called_params
    assert len(lots) == 1
    assert metadata["transport"] == "public-nextjs-html"
    assert metadata["per_page"] == 24
    assert lots[0].raw_data["transport"] == "public-nextjs-html"


def test_torgi_russia_connector_uses_verified_internal_region_filter_ids() -> None:
    connector = TorgiRussiaConnector()
    assert connector._region_ids == list(PUBLIC_REGION_FILTER_IDS)
    assert connector._region_ids[0] == 1
    assert connector._region_ids[-1] == 86
    assert len(connector._region_ids) == 86
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[80] == "83"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[81] == "86"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[82] == "87"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[83] == "89"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[84] == "82"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[85] == "92"
    assert PUBLIC_REGION_FILTER_EXPECTATIONS[86] is None


def test_torgi_russia_connector_pages_each_region_without_legacy_region_api() -> None:
    connector = TorgiRussiaConnector()
    connector._region_ids = [33, 76]
    calls = []

    def search(filters):
        calls.append((filters.region_id, filters.page))
        return [], {
            "has_more": filters.region_id == 33 and filters.page == 1,
            "total_pages": 2 if filters.region_id == 33 else 1,
        }

    connector.client.search_lots = search
    first = asyncio.run(connector.search(TorgiRussiaSearchFilters()))
    second = asyncio.run(connector.search(TorgiRussiaSearchFilters(), first.next_cursor))
    third = asyncio.run(connector.search(TorgiRussiaSearchFilters(), second.next_cursor))

    assert calls == [(33, 1), (33, 2), (76, 1)]
    assert first.next_cursor == "region:0:2"
    assert second.next_cursor == "region:1:1"
    assert third.next_cursor is None
    assert third.metadata["regions_total"] == 2
    assert third.metadata["progress_total"] == 2


def test_torgi_russia_legacy_numeric_cursor_maps_to_first_region() -> None:
    assert TorgiRussiaConnector._decode_cursor("17") == (0, 17)
    assert TorgiRussiaConnector._decode_cursor("region:5:17") == (5, 17)


def test_torgi_russia_accepts_shifted_internal_region_filter_id() -> None:
    item = _new_site_lot(region_title="Ненецкий автономный округ")
    html = _nextjs_search_html([item], total=1)

    class Response:
        url = "https://xn----etbpba5admdlad.xn--p1ai/search?page=1&regions%5B%5D=80"
        text = html

        def raise_for_status(self):
            return None

    class Session:
        headers = {}

        def get(self, _url, *, params=None, timeout=None):
            return Response()

    lots, metadata = TorgiRussiaClient(session=Session()).search_lots(
        TorgiRussiaSearchFilters(region_id=80)
    )

    assert len(lots) == 1
    assert lots[0].region_slug == "83"
    assert metadata["region_id"] == 80


def test_torgi_russia_accepts_other_territories_source_bucket() -> None:
    item = _new_site_lot(
        region_title="Иные территории, включая город и космодром Байконур"
    )
    html = _nextjs_search_html([item], total=1)

    class Response:
        url = "https://xn----etbpba5admdlad.xn--p1ai/search?page=1&regions%5B%5D=86"
        text = html

        def raise_for_status(self):
            return None

    class Session:
        headers = {}

        def get(self, _url, *, params=None, timeout=None):
            return Response()

    lots, metadata = TorgiRussiaClient(session=Session()).search_lots(
        TorgiRussiaSearchFilters(region_id=86)
    )

    assert len(lots) == 1
    assert lots[0].region_slug is None
    assert lots[0].region_name == "Иные территории, включая город и космодром Байконур"
    assert metadata["region_id"] == 86


def test_torgi_russia_rejects_ignored_region_filter() -> None:
    item = _new_site_lot(region_title="Тамбовская область")
    html = _nextjs_search_html([item], total=1)

    class Response:
        url = "https://xn----etbpba5admdlad.xn--p1ai/search?page=1&regions%5B%5D=33"
        text = html

        def raise_for_status(self):
            return None

    class Session:
        headers = {}

        def get(self, _url, *, params=None, timeout=None):
            return Response()

    client = TorgiRussiaClient(session=Session())
    try:
        client.search_lots(TorgiRussiaSearchFilters(region_id=33))
    except RuntimeError as exc:
        assert "public region filter was not applied" in str(exc)
    else:
        raise AssertionError("ignored region filter must fail closed")
