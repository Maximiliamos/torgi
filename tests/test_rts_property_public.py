"""Contract tests for RTS property-sale Angular DOM (anonymized synthetic HTML).

Only saved page structure is represented. No real debtor names, contacts,
original HTML, cookies, network requests or production writes.
"""
from __future__ import annotations

import pytest

from bankrotai.connectors.registry.rts_property_public import (
    GRID, RtsEvidenceError, checked_lot_url, money,
    parse_grid, parse_lot_detail,
)

LOT_UUID = "beabcc8f-d9bf-4b5c-a53d-33f8b9be3836"
URL = f"https://it2.rts-tender.ru/trades/lotCard/{LOT_UUID}"
NOTICE = "22000086100000000619"


def card(*, uuid=LOT_UUID, lot="6", regulation="Реализация арестованного имущества",
         title="Жилой дом 83,6 кв.м. и земельный участок"):
    link = f"https://it2.rts-tender.ru/trades/lotCard/{uuid}"
    return f"""
    <div rts-at="all-trades-lot-card" class="it-main-grid-card">
      <app-lot-state-badge rts-at="lot-card-lot-state">
        <span>Опубликован</span></app-lot-state-badge>
      <p rts-at="lot-card-procurement-regulation">{regulation}</p>
      <a rts-at="lot-card-trade-number"
        href="https://it2.rts-tender.ru/trades/test">{NOTICE}</a>
      <a rts-at="lot-card-lot-number" href="{link}">Лот {lot}</a>
      <a rts-at="lot-card-lot-name" href="{link}">{title}</a>
      <div class="it-main-grid-card__additional-section">
        <span class="rts-text_h3">3 514 784,00 ₽</span>
        <p>Задаток 175 500,00 ₽</p>
      </div>
    </div>
    """


def detail(*, uuid=LOT_UUID, price="3 514 784,00 руб.", 
           notice=NOTICE, code="22000086100000000619006"):
    return f"""
    <div class="procedure-title">
      <h3><a data-type-id="procedureCardNumberLink"
          href="https://torgi.gov.ru/new/public/notices/view/{notice}">{notice}</a></h3>
      <p data-type-id="procedureCardProcedureType">
        Реализация арестованного имущества/Аукцион</p>
    </div>
    <div class="lot-card">
      <span data-type-id="lotCardDescriptionLotStatus">Опубликован</span>
      <a data-type-id="lotCardDescriptionLotNumber"
         href="https://it2.rts-tender.ru/trades/lotCard/{uuid}">{code}</a>
      <h2 data-type-id="lotCardDescriptionLotName">Жилой дом 83,6 кв.м.</h2>
      <h2 data-type-id="lotCardDescriptionLotPrice">{price}</h2>
      <p class="lot-card__category-content">Здания</p>
      <p data-type-id="lotCardDescriptionPropertyDescription">
        Жилой дом кад. № 58:05:0700602:5953 и земельный участок
        58:05:0700602:5765, для ИЖС.</p>
      <span data-type-id="countdownDate">(29.10.2026 10:00 МСК)</span>
    </div>
    """


def test_grid_has_independent_unique_lots_and_retains_auction_regime():
    html = f"<app-all-trades-page>Найдено: более 1 000 лотов {card()}" + card(
        uuid="e63b29f2-f6cc-4169-927b-e9fc8d4c9de7",
        lot="1", title="Жилое помещение (квартира) 45,9 кв.м."
    ) + card(
        uuid="514aeaae-5bb6-44c3-8e44-54d1dee1267a",
        regulation="Размещение нестационарных объектов",
        title="Право на размещение объекта бизнеса в городском парке",
    ) + "</app-all-trades-page>"
    result = parse_grid(html)
    assert result["observed_cards"] == 3
    assert result["reported_total_label"] == "более 1 000"
    assert result["rejected"] == {"non_property": 1}
    assert len(result["property_candidates"]) == 2
    row = result["property_candidates"][0]
    assert row["lot_uuid"] == LOT_UUID
    assert row["lot_number"] == "6"
    assert row["notice_number"] == NOTICE
    assert row["regulation"] == "Реализация арестованного имущества"
    assert row["listing_price_rub"] == "3514784.00"
    assert row["detail_url"] == URL
    assert row["notice_url"].endswith(NOTICE)
    assert row["ready_for_ingestion"] is False
    assert result["complete_source_inventory"] is False
    assert result["safe_for_archival"] is False


def test_detail_preserves_notice_lot_id_and_both_cadastres():
    row = parse_lot_detail(detail(), URL)
    assert row["source_lot_code"] == "22000086100000000619006"
    assert row["notice_number"] == NOTICE
    assert row["lot_number"] == "6"
    assert row["lot_uuid"] == LOT_UUID
    assert row["initial_price_rub"] == "3514784.00"
    assert row["current_price_rub"] is None
    assert row["source_status_text"] == "Опубликован"
    assert row["regulation"] == "Реализация арестованного имущества/Аукцион"
    assert row["cadastral_numbers"] == [
        "58:05:0700602:5953", "58:05:0700602:5765"
    ]
    assert row["notice_url"] == f"https://torgi.gov.ru/new/public/notices/view/{NOTICE}"
    assert row["coordinates"] is None
    assert row["ready_for_ingestion"] is False


@pytest.mark.parametrize("kwargs", [
    {"uuid": "e63b29f2-f6cc-4169-927b-e9fc8d4c9de7"},
    {"price": "0 руб."},
    {"notice": "22000086100000000620"},
    {"code": "22000086100000000619007"},
])
def test_contradictory_detail_fails_closed(kwargs):
    with pytest.raises(RtsEvidenceError):
        parse_lot_detail(detail(**kwargs), URL)


@pytest.mark.parametrize("url", [
    "https://evil.org/trades/lotCard/" + LOT_UUID,
    "https://it2.rts-tender.ru.evil.org/trades/lotCard/" + LOT_UUID,
    "http://it2.rts-tender.ru/trades/lotCard/" + LOT_UUID,
    "https://it2.rts-tender.ru/trades/lotCard/" + LOT_UUID + "?x=1",
    "https://it2.rts-tender.ru/trades/lotCard/" + LOT_UUID + "#x",
])
def test_reject_injected_or_noncanonical_urls(url):
    with pytest.raises(RtsEvidenceError):
        checked_lot_url(url)


def test_empty_grid_or_detail_cannot_fabricate_lots():
    with pytest.raises(RtsEvidenceError):
        parse_grid("<app-root>Angular preloader</app-root>")
    with pytest.raises(RtsEvidenceError):
        parse_lot_detail("<app-root>Angular preloader</app-root>", URL)


def test_russian_money_is_exact():
    assert money("3 514 784,00 руб.") == "3514784.00"
    assert money("1\u00a0189\u00a0607,40\u00a0₽") == "1189607.40"
    assert money("0 руб.") is None
    assert money("Процедура 22000086100000000619") is None
