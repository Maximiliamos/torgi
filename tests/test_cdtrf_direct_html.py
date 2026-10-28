"""Offline contracts for CDTRF public HTML/Nuxt extracted from real saved pages.

Fixtures omit debtor names, contacts and legal disclosures. Source files:
torgi.cdtrf.ru/ and torgi.cdtrf.ru/trades/390364 (October 2026).
"""
from __future__ import annotations

import json

import pytest

from bankrotai.connectors.registry.cdtrf_public import (
    CdtrfEvidenceError, CdtrfPublicPilot, checked_url,
    money, parse_catalogue, parse_detail,
)

URL = "https://torgi.cdtrf.ru/trades/390364"


def catalog():
    return """
    <div class="main-view-line">Найдено: 377303 лота</div>
    <div class="catalog-card">
        <div class="catalog-card__code">Код торгов: 390364</div>
        <div class="catalog-card__status">Статус: Торги объявлены</div>
        <div class="catalog-card__type">Публичное предложение</div>
        <a class="catalog-card__name" href="https://torgi.cdtrf.ru/trades/390364">нежилое помещение (гараж)</a>
        <div class="catalog-card__price-val">675 000 ₽</div>
        <div class="catalog-card__trade-info">Дата приема заявок:
           29.10.2026 00:00:00 - 02.12.2026 23:59:00</div>
    </div>
    <div class="pagination__nav"><button class="pagination__item active">1</button>
    <button class="pagination__item">18866</button></div>
    """


def build_detail(*, tamper_price=False, tamper_id=False):
    """Build a minimal Nuxt reference graph, same shape as real SSR JSON."""
    nodes = [None, None]

    def add(value):
        nodes.append(value)
        return len(nodes) - 1

    def ref_mapping(values):
        return add({key: add(value) for key, value in values.items()})

    period = ref_mapping({
        "startTime": "29.10.2026 00:00:00",
        "endTime": "02.11.2026 23:59:00",
        "price": "675000,00",
    })
    periods = add([period])
    lot_fields = {
        "tradeId": "390364",
        "tradeLotId": "390899",
        "lotNumber": 1,
        "name": "нежилое помещение (гараж)",
        "priceBegin": "675000,00",
        "regionID": 32,
        "latitude": "0.0000000",
        "longitude": "0.0000000",
        "lotInfo": "<p>Нежилое помещение, кадастр 32:10:0010101:42</p>",
    }
    lot_node = ref_mapping(lot_fields)
    nodes[lot_node]["lotScheduleItems"] = periods
    info = ref_mapping({
        "tradeId": "390364" if not tamper_id else "390365",
        "tradeStatus": "Declare",
        "tradeStatusDescription": "Торги объявлены",
        "tradeType": "PubProposal",
        "tradeTypeDescription": "Открытое публичное предложение",
        "requestTimeBegin": "29.10.2026 00:00",
        "requestTimeEnd": "02.12.2026 23:59",
        "winnerTime": "03.12.2026 12:00",
        "idEFRSB": "25293724",
    })
    nodes[info]["lot"] = lot_node
    data = add({"$test": info})
    wrapper = add(["ShallowReactive", data])
    nodes[1] = {"data": wrapper}
    return f"""
    <div class="lot__header">
      <div class="lot__header-code">Код торгов: 390364</div>
      <h1>нежилое помещение (гараж)</h1>
    </div>
    <div class="lot__info-badge">
      <div class="lot__info-badge-label">Категория имущества</div>
      <div class="lot__info-badge-val">Прочие постройки</div>
    </div>
    <div class="lot__info-badge">
      <div class="lot__info-badge-label">Адрес местонахождения имущества</div>
      <div class="lot__info-badge-val">Брянская область</div>
    </div>
    <div class="lot-sidebar__price-val">{"675 001,00" if tamper_price else "675 000,00"} ₽</div>
    <script id="__NUXT_DATA__" type="application/json">{json.dumps(nodes, ensure_ascii=False)}</script>
    """


def test_catalogue_recovers_distinct_trade_id_and_records_no_complete_pagination():
    report = parse_catalogue(catalog())
    assert report["visible_cards"] == 1
    assert report["accepted_cards"] == 1
    assert report["reported_total_lots"] == 377303
    assert report["displayed_last_page"] == 18866
    assert report["pagination_verified"] is False
    row = report["page_observations"][0]
    assert row["trade_id"] == "390364"
    assert row["trade_lot_id"] is None
    assert row["detail_url"] == URL
    assert row["property_hint"] is True
    assert row["initial_price_rub"] == "675000.00"
    assert row["application_end"] == "02.12.2026 23:59:00"


def test_detail_uses_stable_trade_lot_identity_and_price_schedule_not_fake_current_price():
    item = parse_detail(build_detail(), URL)
    assert item["trade_id"] == "390364"
    assert item["trade_lot_id"] == "390899"
    assert item["lot_number"] == "1"
    assert item["external_id"] == "cdtrf:390899"
    assert item["initial_price_rub"] == "675000.00"
    assert item["current_price_rub"] is None
    assert item["source_status_code"] == "Declare"
    assert item["efrsb_message_id"] == "25293724"
    assert item["region_label"] == "Брянская область"
    assert item["region_id"] == 32
    assert item["cadastral_number"] == "32:10:0010101:42"
    assert item["coordinates"] is None
    assert item["price_periods"] == [{
        "start": "29.10.2026 00:00:00",
        "end": "02.11.2026 23:59:00",
        "price_rub": "675000.00",
    }]
    assert item["ready_for_ingestion"] is False


@pytest.mark.parametrize("html", [
    build_detail(tamper_price=True), build_detail(tamper_id=True),
])
def test_mismatched_card_fails_closed(html):
    with pytest.raises(CdtrfEvidenceError):
        parse_detail(html, URL)


@pytest.mark.parametrize("malicious_url", [
    "https://evil.example/trades/390364",
    "http://torgi.cdtrf.ru/trades/390364",
    "https://torgi.cdtrf.ru/trades/390364?token=private",
    "https://torgi.cdtrf.ru.evil.example/trades/390364",
])
def test_restrict_detail_to_official_https_source(malicious_url):
    with pytest.raises(CdtrfEvidenceError):
        checked_url(malicious_url)


def test_missing_nuxt_data_does_not_fabricate_price_or_status():
    with pytest.raises(CdtrfEvidenceError, match="No CDTRF SSR"):
        parse_detail('<div class="lot__header"><h1>Гараж</h1></div>', URL)


def test_money_is_decimal_and_does_not_steal_lot_id():
    assert money("675 000,00 ₽") == "675000.00"
    assert money("675000,00") == "675000.00"
    assert money("Код торгов: 390364") is None
    assert money("0 ₽") is None


class Response:
    headers = {"Content-Type": "text/html"}
    status_code = 200

    def __init__(self, html):
        self.text = html
        self.content = html.encode("utf-8")

    def raise_for_status(self):
        return None


class Session:
    def __init__(self):
        self.urls = []

    def get(self, url, *, timeout, allow_redirects, headers):
        self.urls.append(url)
        assert allow_redirects is False
        return Response(catalog() if url == "https://torgi.cdtrf.ru/" else build_detail())


def test_pilot_reads_source_but_cannot_archive_or_mutate_db(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_public as module

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    session = Session()
    report = CdtrfPublicPilot(session=session).preview(max_lots=1)
    assert report["database_writes"] == 0
    assert report["complete_source_inventory"] is False
    assert report["safe_for_archival"] is False
    assert report["inspected"] == 1
    assert report["detail_records"][0]["trade_lot_id"] == "390899"
    assert session.urls == ["https://torgi.cdtrf.ru/", URL]
