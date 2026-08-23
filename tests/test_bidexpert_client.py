import asyncio

from bankrotai.bidexpert import BidExpertClient
from bankrotai.connectors.registry.bidexpert import BidExpertConnector
from bankrotai.scraper_contracts import BidExpertSearchFilters


def test_bidexpert_real_sample_excludes_leases_and_keeps_real_estate() -> None:
    # Exact public card structure taken from bidexpert_realty_search.html.
    html = """
    <div class="bid-item"><a href="https://bidexpert.ru/bids/lot/?n=1072129"><div class="lot-num">Лот №1072129</div>
      <div class="title">Лот № 1 Объект аренды, площадь 159,30 кв.м.</div>
      <div class="start-price">1 215 911 ₽</div><div class="application-submit-end"><span>21-09-2026, 13:00 (29 дней)</span></div></a></div>
    <div class="bid-item"><a href="https://bidexpert.ru/bids/lot/?n=1072939"><div class="lot-num">Лот №1072939</div>
      <div class="title">Земельный участок площадью 284 кв.м, кадастровый номер: 64:17:190312:13, по адресу: Саратовская область, г. Красный Кут, ул. Комсомольская, д. 36.</div>
      <div class="start-price">2 126 167 ₽</div><div class="application-submit-end"><span>22-09-2026, 14:00 (30 дней)</span></div></a></div>
    <span class="pages">Страница 1 из 465</span>
    """
    lots = BidExpertClient().parse_listing_html(html, filters=BidExpertSearchFilters(category="realty"))
    assert lots
    assert all("аренды" not in lot.title.lower() for lot in lots)
    known = next(lot for lot in lots if lot.external_id == "bidexpert:1072939")
    assert known.cadastral_number == "64:17:190312:13"
    assert known.start_price == 2126167.0
    assert known.application_deadline and known.application_deadline.year == 2026


def test_bidexpert_all_cursor_scans_realty_then_land() -> None:
    class Client:
        def search_lots(self, filters):
            return [], {"has_more": False, "total_pages": 1}

    connector = BidExpertConnector()
    connector.client = Client()
    first = asyncio.run(connector.search(BidExpertSearchFilters(category="all")))
    second = asyncio.run(connector.search(BidExpertSearchFilters(category="all"), first.next_cursor))

    assert first.metadata["category_phase"] == "realty"
    assert first.next_cursor == "land:1"
    assert second.metadata["category_phase"] == "land"
    assert second.next_cursor is None
