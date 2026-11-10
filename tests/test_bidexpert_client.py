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
    assert known.address == "Саратовская область, г. Красный Кут, ул. Комсомольская, д. 36"
    assert known.start_price == 2126167.0
    assert known.application_deadline and known.application_deadline.year == 2026


def test_bidexpert_excludes_explicit_lease_transactions_but_keeps_sale_encumbrances() -> None:
    html = """
    <div class="bid-item"><a href="/bids/lot/?n=1"><div class="title">К аренде предлагаются нежилые помещения площадью 404 кв. м.</div></a></div>
    <div class="bid-item"><a href="/bids/lot/?n=2"><div class="title">Предмет аукциона – размер ежегодной арендной платы за земельный участок.</div></a></div>
    <div class="bid-item"><a href="/bids/lot/?n=3"><div class="title">Здание продаётся, земельный участок под ним находится в аренде.</div></a></div>
    """
    lots = BidExpertClient().parse_listing_html(html, filters=BidExpertSearchFilters(category="realty"))

    assert [lot.external_id for lot in lots] == ["bidexpert:3"]


def test_bidexpert_all_cursor_scans_realty_then_land() -> None:
    class Client:
        def search_lots(self, filters):
            return [], {"has_more": False, "total_pages": 1}

    connector = BidExpertConnector()
    connector.client = Client()
    first = asyncio.run(connector.search(BidExpertSearchFilters(category="all")))
    second = asyncio.run(connector.search(BidExpertSearchFilters(category="all"), first.next_cursor))

    assert first.metadata["category_phase"] == "realty"
    assert first.metadata["requested_category_group"] == "realty"
    assert first.next_cursor == "land:1"
    assert second.metadata["category_phase"] == "land"
    assert second.metadata["requested_category_group"] == "land"
    assert second.next_cursor is None


def test_bidexpert_address_keeps_common_abbreviations_and_house_number() -> None:
    samples = {
        36190: (
            "Продажа имущества по адресу: Российская Федерация, Смоленская область, "
            "д. Пигулино, ул. Нижняя, земельный участок 86А.",
            "Российская Федерация, Смоленская область, д. Пигулино, ул. Нижняя, земельный участок 86А",
        ),
        36183: (
            "Объект расположен по адресу: Саратовская область, Новоузенский район, "
            "с. Куриловка, ул. Центральная, д. 12.",
            "Саратовская область, Новоузенский район, с. Куриловка, ул. Центральная, д. 12",
        ),
        36182: (
            "Недвижимость по адресу: Ивановская область, Заволжский район, "
            "д. Новлянское, ул. Полевая, д. 7.",
            "Ивановская область, Заволжский район, д. Новлянское, ул. Полевая, д. 7",
        ),
        36167: (
            "Лот по адресу: Саратовская область, г. Новоузенск, ул. Рабочая, д. 3.",
            "Саратовская область, г. Новоузенск, ул. Рабочая, д. 3",
        ),
        36152: (
            "Имущество по адресу: Волгоградская область, ст. Кумылженская, "
            "ул. Советская, д. 20.",
            "Волгоградская область, ст. Кумылженская, ул. Советская, д. 20",
        ),
    }
    html = "".join(
        f'<div class="bid-item"><a href="/bids/lot/?n={lot_id}">'
        f'<div class="title">{title}</div></a></div>'
        for lot_id, (title, _expected) in samples.items()
    )

    lots = BidExpertClient().parse_listing_html(html, filters=BidExpertSearchFilters(category="realty"))

    assert {lot.external_id: lot.address for lot in lots} == {
        f"bidexpert:{lot_id}": expected for lot_id, (_title, expected) in samples.items()
    }


def test_bidexpert_isolates_adjacent_cards_and_builds_working_absolute_url() -> None:
    html = """
    <div class="lots-wrapper">
      <div class="bid-item">
        <a href="/bids/lot/?n=1120320">
          <div class="title">Чужой земельный участок</div>
          <div>315 957 ₽</div>
        </a>
      </div>
      <div class="bid-item">
        <a href="/bids/lot/?n=1121014">
          <div class="title">Нежилое помещение, кадастровый номер 50:46:0010101:10</div>
          <div>4 250 000 ₽</div>
        </a>
      </div>
    </div>
    """
    lots = BidExpertClient().parse_listing_html(
        html, filters=BidExpertSearchFilters(category="realty")
    )

    reported = next(lot for lot in lots if lot.external_id == "bidexpert:1121014")
    assert reported.title.startswith("Нежилое помещение")
    assert "Чужой земельный" not in reported.description
    assert reported.start_price == 4_250_000
    assert reported.lot_url == "https://bidexpert.ru/bids/lot/?n=1121014"
    assert reported.source_url == reported.lot_url


def test_bidexpert_title_region_is_persisted_when_address_and_cadastre_are_absent() -> None:
    html = """
    <div class="bid-item"><a href="/bids/lot/?n=944718">
      <div class="title">Лот №944718 Липецкая область Прием заявок Земельные участки земельный участок</div>
      <div class="application-submit-end"><span>24-02-2027, 21:00</span></div>
    </a></div>
    """
    lot = BidExpertClient().parse_listing_html(
        html, filters=BidExpertSearchFilters(category="land")
    )[0]
    assert lot.region_slug == "48"
    assert lot.region_name == "Липецкая область"
    assert lot.raw_data["region_code"] == "48"

def test_bidexpert_detail_uses_lot_card_location_not_footer_or_related_cards() -> None:
    html = """
    <html><head><title>
      Информация о праве граждан участвовать в аукционе, лот №944717
    </title></head><body>
      <div class="section-title">Земельный участок</div>
      <div class="bid-card">
        <div class="bid-card-content-item">
          <div class="title">Описание лота:</div>
          <div class="content">земельный участок</div>
        </div>
        <div class="bid-card-content-item">
          <div class="title">Месторасположение:</div>
          <div class="content">
            Липецкая область, обл Липецкая, м.о. Липецкий, с Ивово, ул Лесная
          </div>
        </div>
      </div>
      <div class="bids-card-description">
        Чужой земельный участок, кадастровый номер 22:43:020001:1274
      </div>
      <div class="footer">
        Адрес 117545, г. Москва, ш. Варшавское, д. 129 к. 3
      </div>
    </body></html>
    """
    lot = BidExpertClient().parse_detail_html(
        html,
        lot_id="944717",
        endpoint="https://bidexpert.ru/bids/lot/?n=944717",
    )

    assert lot.address == (
        "Липецкая область, обл Липецкая, м.о. Липецкий, с Ивово, ул Лесная"
    )
    assert lot.region_slug == "48"
    assert lot.region_name == "Липецкая область"
    assert lot.cadastral_number is None
    assert "Москва" not in lot.address
    assert lot.raw_data["source"] == "bidexpert_detail_html"


def test_bidexpert_detail_ignores_conditional_cadastral_number_and_keeps_asset_address() -> None:
    html = """
    <html><head><title>
      Земельный участок с условным кадастровым номером 67:14:1380101:ЗУ1,
      лот №919738
    </title></head><body>
      <div class="section-title">
        Земельный участок с условным кадастровым номером 67:14:1380101:ЗУ1
      </div>
      <div class="bid-card">
        <div class="bid-card-content-item">
          <div class="title">Описание лота:</div>
          <div class="content">
            Земельный участок с условным кадастровым номером 67:14:1380101:ЗУ1
            площадь 1231 по адресу Российская Федерация, Смоленская область,
            Починковский муниципальный округ, напротив д. 40
          </div>
        </div>
        <div class="bid-card-content-item">
          <div class="title">Месторасположение:</div>
          <div class="content">
            Смоленская область, обл Смоленская, м.о. Починковский, д Шанталово
          </div>
        </div>
      </div>
    </body></html>
    """
    lot = BidExpertClient().parse_detail_html(
        html,
        lot_id="919738",
        endpoint="https://bidexpert.ru/bids/lot/?n=919738",
    )

    assert lot.cadastral_number is None
    assert lot.address is not None
    assert "Смоленская область" in lot.address
    assert "ЗУ1" not in (lot.cadastral_number or "")


def test_bidexpert_connector_exposes_exact_detail_without_bulk_enrichment() -> None:
    connector = BidExpertConnector()
    sentinel = object()
    calls = []

    def fake_fetch(external_id):
        calls.append(external_id)
        return sentinel

    connector.client.fetch_lot_detail = fake_fetch
    result = asyncio.run(connector.fetch_lot("bidexpert:944717"))

    assert result is sentinel
    assert calls == ["bidexpert:944717"]
    assert "detail" in connector.capabilities
    assert "detail_enrichment" not in connector.capabilities

