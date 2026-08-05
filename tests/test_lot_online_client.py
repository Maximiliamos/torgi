from bankrotai.scraper_contracts import LotOnlineSearchFilters
from bankrotai.domain import NormalizedLot
from bankrotai.scrapers import LotOnlineClient, is_sale_real_estate_lot


LISTING_HTML = """
<html><body>
  <div class="ty-grid-list__item">
    <input name="product_data[1759164][product_id]" value="1759164">
    <img class="ty-pict cm-image" data-src="https://catalog.lot-online.ru/cdn/bkr/353x254/lot-1759164.jpg">
    <a class="product-title"
       href="/index.php?dispatch=products.view&amp;product_id=1759164"
       title="Земельный участок, Ярославская область, кадастровый номер 76:23:010101:42">
       Земельный участок...
    </a>
    <span class="ty-price-num">7&nbsp;090&nbsp;000</span>
    <span class="ty-grid-list__product-code">РАД-453717</span>
  </div>
  <div class="ty-pagination">
    <span class="ty-pagination__selected">1</span>
    <a href="/index.php?dispatch=categories.view&amp;category_id=1&amp;page=2">2</a>
  </div>
</body></html>
"""


def test_builds_public_catalogue_query_for_yaroslavl() -> None:
    filters = LotOnlineSearchFilters(
        search_text="склад",
        region_feature="24392",
        archive_mode="false",
        page=2,
        page_size=96,
    )

    params = LotOnlineClient()._build_query_params(filters)

    assert params["dispatch"] == "categories.view"
    assert params["category_id"] == "1"
    assert params["features_hash"] == "171-24392"
    assert params["filter_fields[is_archive]"] == "false"
    assert params["filter_fields[is_aggregator]"] == "all"
    assert params["q"] == "склад"
    assert params["page"] == "2"


def test_yaroslavl_region_accepts_official_code_and_name() -> None:
    for value in ("76", "Ярославская область", "yaroslavl"):
        params = LotOnlineClient()._build_query_params(LotOnlineSearchFilters(region_feature=value))
        assert params["features_hash"] == "171-24392"


def test_parses_listing_card_into_normalized_lot() -> None:
    filters = LotOnlineSearchFilters(region_feature="24392", archive_mode="false")

    lots, meta = LotOnlineClient()._parse_listing_html(LISTING_HTML, filters=filters)

    assert len(lots) == 1
    lot = lots[0]
    assert lot.external_id == "lot-online:1759164"
    assert lot.source_system == "lot-online.ru"
    assert lot.region_slug == "76"
    assert lot.region_name == "Ярославская область"
    assert lot.start_price == 7_090_000
    assert lot.current_price == 7_090_000
    assert lot.auction_status == "active"
    assert lot.procedure_number == "РАД-453717"
    assert lot.cadastral_number == "76:23:010101:42"
    assert lot.raw_data["image_url"] == "https://catalog.lot-online.ru/cdn/bkr/353x254/lot-1759164.jpg"
    assert lot.raw_data["image_urls"] == ["https://catalog.lot-online.ru/cdn/bkr/353x254/lot-1759164.jpg"]
    assert lot.platform_name == "РАД / ЛОТ-ОНЛАЙН"
    assert meta == {"has_more": True, "total_pages": 2}


def test_archive_mode_is_validated() -> None:
    client = LotOnlineClient()

    try:
        client._build_query_params(LotOnlineSearchFilters(archive_mode="invalid"))
    except ValueError as exc:
        assert "archive_mode" in str(exc)
    else:
        raise AssertionError("invalid archive mode must be rejected")


def test_sale_filter_rejects_shares_and_cultural_heritage() -> None:
    def lot(title: str) -> NormalizedLot:
        return NormalizedLot(
            external_id=title,
            source="test",
            source_system="test",
            title=title,
            description=title,
            category="real_estate",
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number=None,
            vin=None,
            area=None,
            start_price=None,
            current_price=None,
            auction_status="active",
            lot_url=None,
            source_url=None,
            detail_level="search",
            raw_data={},
        )

    assert not is_sale_real_estate_lot(lot("16/131 доля в праве на нежилое помещение"))
    assert not is_sale_real_estate_lot(lot("Объект культурного наследия — ансамбль усадьбы"))
    assert is_sale_real_estate_lot(lot("Нежилое помещение, г. Сасово, ул. Ленина, д. 7"))


def test_fetch_detail_fields_reads_structured_address_and_cadastre() -> None:
    class Response:
        content = """
        <html><head><title>Нежилое помещение</title></head><body>
          <dl><div><dt>Адрес</dt><dd>Рязанская область, г. Сасово, ул. Ленина, д. 7</dd></div></dl>
          <div class="ty-product__full-description">
            Нежилое помещение, кадастровый номер 62:27:0010101:15
          </div>
        </body></html>
        """.encode("utf-8")
        url = "https://catalog.lot-online.ru/lot/1"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Session:
        headers: dict = {}

        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    fields = LotOnlineClient(session=Session()).fetch_detail_fields(Response.url)

    assert fields["address"] == "Рязанская область, г. Сасово, ул. Ленина, д. 7"
    assert fields["cadastral_numbers"] == ["62:27:0010101:15"]
