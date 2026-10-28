"""Offline tests for Sberbank-AST UTP sale card based on anonymized DOM fixture.

No downloaded customer HTML, credentials, contact info or sensitive data in Git.
"""
from __future__ import annotations

import pytest

from bankrotai.connectors.registry.sber_ast_property import (
    SberAstEvidenceError, inspect_united_purchase_list, money,
    parse_property_detail, validate_view_url,
)

URL = "https://utp.sberbank-ast.ru/Property/NBT/PurchaseView/30/0/0/4271626"


def span(id_: str, value: str) -> str:
    return f'<span id="{id_}">{value}</span>'


def sample(*, tamper_initial=False, tamper_period=False, duplicate_id=False,
           lot_number="1", id_value="3448676") -> str:
    periods = []
    amounts = [2050800, 1982440, 1914080, 1845720, 1777360,
               1709000, 1640640, 1572280, 1503920, 1435560, 1367200]
    if tamper_period:
        amounts[3] = 3000000
    for n, amount in enumerate(amounts, 1):
        periods.append(
            f'<tr id="tBodyBids_Periodsrow_{n}">'
            f'<td><span content="leaf:PeriodNo">{n}</span></td>'
            f'<td><span content="leaf:PeriodStartDate">01.02.2027 10:00</span></td>'
            f'<td><span content="leaf:PeriodStopDate">01.02.2027 10:20</span></td>'
            f'<td><span content="leaf:PeriodPrice">{amount}.00</span></td>'
            f'<td><span content="leaf:PeriodStep">68360.00</span></td>'
            f'<td><span content="leaf:PeriodStatus">Новый</span></td>'
            '</tr>'
        )
    return "\n".join((
        span("PurchaseInfoView_PurchaseCode", "SBR065-2610280004"),
        span("PurchaseInfoView_PurchaseName", "Продажа нежилого помещения и земли"),
        span("PurchaseInfoView_PurchaseTypeName", "Голландский аукцион (с ЭП)"),
        span("PurchaseInfoView_RegionIdName", "Ставропольский край"),
        span("Bids_BidName", "Право купить нежилое помещение и земельный участок"),
        f'<input id="Bids_BidId" type="hidden" value="{id_value}">',
        f'<input id="Bids_BidId" type="hidden" value="4567890">' if duplicate_id else "",
        span("Bids_BidNo", lot_number),
        span("Bids_BidStatus", "Прием заявок"),
        span("Bids_BidPropertyTypeName", "Имущественный комплекс"),
        span("Bids_BidPriceNotReq", "2990000.00" if tamper_initial else "2  050 800.00"),
        span("Bids_BidMinPrice", "1367200.00"),
        span("Bids_BidRequestCoverAmountDeposit", "205 080.00"),
        span("Bids_BidArea", "141.90"),
        span("Bids_BidAreaZU", "236.87"),
        span("PurchasePlan_RequestStartDate", "28.10.2026 15:00"),
        span("PurchasePlan_RequestStopDate", "28.01.2027 15:00"),
        span("PurchasePlan_RequestReviewDate", "29.01.2027 15:00"),
        span("PurchasePlan_AuctionMinPeriodsDate", "01.02.2027 10:00"),
        '<table id="Bids_PeriodsRO">' + "\n".join(periods) + '</table>',
        '<table id="Docs_DocstblDoc"><tr><td><span id="Docs_flDocsFileName9">'
        'auction_documentation.pdf</span></td></tr></table>',
    ))


def test_property_card_has_three_distinct_ids_and_fixed_price_schedule():
    row = parse_property_detail(sample(), URL)
    assert row["purchase_id"] == "4271626"
    assert row["trade_id"] == "SBR065-2610280004"
    assert row["lot_id"] == "3448676"
    assert row["lot_number"] == "1"
    assert row["external_id"] == "sber_ast_utp:3448676"
    assert row["source_status_text"] == "Прием заявок"
    assert row["region"] == "Ставропольский край"
    assert row["property_type"] == "Имущественный комплекс"
    assert row["initial_price_rub"] == "2050800.00"
    assert row["minimum_price_rub"] == "1367200.00"
    assert row["deposit_rub"] == "205080.00"
    assert row["area_sqm"] == "141.90"
    assert row["land_area_sqm"] == "236.87"
    assert row["application_end"] == "28.01.2027 15:00"
    assert len(row["price_periods"]) == 11
    assert row["price_periods"][-1]["price_rub"] == "1367200.00"
    assert row["document_names"] == ["auction_documentation.pdf"]
    assert row["coordinates"] is None
    assert row["current_price_rub"] is None
    assert row["ready_for_ingestion"] is False
    assert row["safe_for_archival"] is False
    assert row["pagination_verified"] is False
    assert row["verified_live"] is False


def test_dutch_auction_period_dates_are_chronological_not_lexical():
    # 31 December -> 1 January is valid even though textual dd.mm order falls.
    html = sample().replace(
        'content="leaf:PeriodStartDate">01.02.2027 10:00',
        'content="leaf:PeriodStartDate">31.12.2026 23:55', 1
    ).replace(
        'content="leaf:PeriodStopDate">01.02.2027 10:20',
        'content="leaf:PeriodStopDate">01.01.2027 00:05', 1
    )
    row = parse_property_detail(html, URL)
    assert row["price_periods"][0]["end"] == "01.01.2027 00:05"
    bad = sample().replace(
        'content="leaf:PeriodStartDate">01.02.2027 10:00',
        'content="leaf:PeriodStartDate">99.12.2026 10:00', 1
    )
    with pytest.raises(SberAstEvidenceError, match="Invalid price period dates"):
        parse_property_detail(bad, URL)


@pytest.mark.parametrize("kwargs", [
    {"tamper_initial": True},
    {"tamper_period": True},
    {"duplicate_id": True},
    {"lot_number": "0"},
    {"id_value": ""},
])
def test_corrupt_card_fails_closed(kwargs):
    with pytest.raises(SberAstEvidenceError):
        parse_property_detail(sample(**kwargs), URL)


@pytest.mark.parametrize("bad", [
    "http://utp.sberbank-ast.ru/Property/NBT/PurchaseView/30/0/0/4271626",
    "https://bad.com/Property/NBT/PurchaseView/30/0/0/4271626",
    "https://utp.sberbank-ast.ru.evil.org/Property/NBT/PurchaseView/30/0/0/4271626",
    "https://utp.sberbank-ast.ru/Property/NBT/PurchaseView/30/0/0/4271626?secret=abc",
    "https://utp.sberbank-ast.ru/Property/NBT/RequestCreate/30/3/0/3448676",
    "https://utp.sberbank-ast.ru/Property/NBT/PurchaseView/30/0/0/4271626#priv",
])
def test_strict_property_card_url(bad):
    with pytest.raises(SberAstEvidenceError):
        validate_view_url(bad)


def test_upt_urls_can_have_distinct_sale_sections():
    assert validate_view_url(URL)[1:] == ("30", "4271626")
    other = "https://utp.sberbank-ast.ru/Property/NBT/PurchaseView/43/0/0/4262620"
    assert validate_view_url(other)[1:] == ("43", "4262620")


def test_money_uses_decimal_without_conflating_lot_number():
    assert money("2  050 800.00") == "2050800.00"
    assert money("1\u00a0367\u00a0200,00") == "1367200.00"
    assert money("205 080.00") == "205080.00"
    assert money("SBR065-2610280004") is None


def test_44fz_united_list_is_not_property_sale_inventory():
    view = inspect_united_purchase_list(
        "<body><div>Госзакупки по 44-ФЗ</div>"
        "<div>Поставка лабораторной посуды</div></body>"
    )
    assert view["page_type"] == "united_procurements"
    assert view["visible_property_sale_links"] == 0
    assert view["can_ingest_property_lots"] is False
