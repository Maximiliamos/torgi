from bankrotai.scraper_contracts import LotOnlineSearchFilters
from bankrotai.scrapers import LotOnlineClient


LISTING_HTML = """
<html><body>
  <div class="ty-grid-list__item">
    <input name="product_data[1759164][product_id]" value="1759164">
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
