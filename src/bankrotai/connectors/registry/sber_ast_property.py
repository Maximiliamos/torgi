"""Read-only Sberbank-AST UTP property-sale detail extractor.

Grounded in 2026-10-28 HTML of:
https://utp.sberbank-ast.ru/Property/NBT/PurchaseView/30/0/0/4271626

Do not treat www.sberbank-ast.ru/UnitedPurchaseList.html (mostly 44-FZ
procurements) as the property-sale inventory. A single valid detail is not
evidence of source completeness, status reconciliation or live price.
No network calls, authentication, application submission, or database writes.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

SOURCE = "sberbank-ast.ru"
HOST = "utp.sberbank-ast.ru"
VIEW_PATH = re.compile(
    r"^/Property/NBT/PurchaseView/([1-9]\d{0,2})/0/0/([1-9]\d{3,12})/?$"
)
TRADE_CODE = re.compile(r"^[A-Z0-9]{3,12}-\d{6,14}$")
CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")
DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")
PROPERTY = re.compile(
    r"недвижим|помещени|здани|земельн|участок|квартир|гараж|"
    r"сооружени|имущественный комплекс|машино.?мест|жилой дом",
    re.I,
)


class SberAstEvidenceError(ValueError):
    """Malformed or contradictory property listing evidence."""


def validate_view_url(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    match = VIEW_PATH.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != HOST or match is None
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise SberAstEvidenceError("Not a direct public Sberbank-AST UTP sale detail URL")
    section, purchase_id = match.groups()
    return f"https://{HOST}/Property/NBT/PurchaseView/{section}/0/0/{purchase_id}", section, purchase_id


def _plain(element) -> str:
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() if element else ""


def _value(soup: BeautifulSoup, field_id: str) -> str | None:
    nodes = soup.find_all(id=field_id)
    if len(nodes) != 1:
        return None
    element = nodes[0]
    text = element.get("value") if element.name == "input" else _plain(element)
    return re.sub(r"\s+", " ", text).strip() if text is not None else None


def _required(soup: BeautifulSoup, field_id: str) -> str:
    value = _value(soup, field_id)
    if not value:
        raise SberAstEvidenceError(f"Missing or nonunique UTP field {field_id}")
    return value


def money(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", value)
    if not re.fullmatch(r"\d{1,13}(?:[.,]\d{1,2})?", cleaned):
        return None
    try:
        amount = Decimal(cleaned.replace(",", "."))
        return str(amount.quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _require_money(soup: BeautifulSoup, field_id: str) -> str:
    parsed = money(_required(soup, field_id))
    if parsed is None or Decimal(parsed) <= 0:
        raise SberAstEvidenceError(f"Missing or invalid UTP price {field_id}")
    return parsed


def _period_field(row, content: str) -> str:
    element = row.select_one(f'[content="leaf:{content}"]')
    value = _plain(element)
    if not value:
        raise SberAstEvidenceError(f"Missing UTP period {content}")
    return value


def _price_periods(soup: BeautifulSoup, *, initial: str, minimum: str | None) -> list[dict]:
    result = []
    seen = set()
    for row in soup.select('tr[id^="tBodyBids_Periodsrow_"]'):
        index = _period_field(row, "PeriodNo")
        start = _period_field(row, "PeriodStartDate")
        end = _period_field(row, "PeriodStopDate")
        price = money(_period_field(row, "PeriodPrice"))
        step = money(_period_field(row, "PeriodStep"))
        if (not index.isdecimal() or index in seen or not DATE.fullmatch(start)
                or not DATE.fullmatch(end) or not price or not step
                or Decimal(price) <= 0):
            raise SberAstEvidenceError("Invalid UTP price period")
        seen.add(index)
        result.append({
            "period_number": int(index),
            "start": start, "end": end,
            "price_rub": price, "step_rub": step,
            "source_period_status": _period_field(row, "PeriodStatus"),
        })
    if result:
        if result[0]["period_number"] != 1 or result[0]["price_rub"] != initial:
            raise SberAstEvidenceError("First price period contradicts initial price")
        previous = Decimal(initial)
        floor = Decimal(minimum) if minimum is not None else Decimal(0)
        for position, period in enumerate(result, 1):
            price = Decimal(period["price_rub"])
            if (period["period_number"] != position or price > previous
                    or price < floor or period["start"] > period["end"]):
                raise SberAstEvidenceError("Incomplete/unsorted or contradictory UTP price periods")
            previous = price
    return result


def parse_property_detail(html: str, url: str) -> dict:
    canonical, section, purchase_id = validate_view_url(url)
    soup = BeautifulSoup(html, "html.parser")
    code = _required(soup, "PurchaseInfoView_PurchaseCode")
    if not TRADE_CODE.fullmatch(code):
        raise SberAstEvidenceError("Missing stable procedure number")
    purchase_title = _required(soup, "PurchaseInfoView_PurchaseName")
    lot_title = _required(soup, "Bids_BidName")
    bid_id = _required(soup, "Bids_BidId")
    lot_number = _required(soup, "Bids_BidNo")
    if (not bid_id.isdecimal() or int(bid_id) <= 0
            or not lot_number.isdecimal() or int(lot_number) <= 0):
        raise SberAstEvidenceError("No unique UTP lot identity")
    status = _required(soup, "Bids_BidStatus")
    sale_type = _required(soup, "PurchaseInfoView_PurchaseTypeName")
    region = _required(soup, "PurchaseInfoView_RegionIdName")
    property_type = _required(soup, "Bids_BidPropertyTypeName")
    if not PROPERTY.search(lot_title + " " + property_type):
        raise SberAstEvidenceError("Not a verifiable property-sale lot")
    initial = _require_money(soup, "Bids_BidPriceNotReq")
    minimum = money(_value(soup, "Bids_BidMinPrice"))
    deposit = money(_value(soup, "Bids_BidRequestCoverAmountDeposit"))
    area = money(_value(soup, "Bids_BidArea"))
    land_area = money(_value(soup, "Bids_BidAreaZU"))
    if minimum and Decimal(minimum) > Decimal(initial):
        raise SberAstEvidenceError("Cutoff price exceeds initial price")
    period_rows = _price_periods(soup, initial=initial, minimum=minimum)
    times = {}
    for key, field in (
        ("application_start", "PurchasePlan_RequestStartDate"),
        ("application_end", "PurchasePlan_RequestStopDate"),
        ("application_review", "PurchasePlan_RequestReviewDate"),
        ("auction_start", "PurchasePlan_AuctionMinPeriodsDate"),
    ):
        times[key] = _value(soup, field)
    descriptions = lot_title + " " + purchase_title
    cadastres = list(dict.fromkeys(CADASTRE.findall(descriptions)))
    doc_table = soup.select_one("table#Docs_DocstblDoc")
    files = []
    if doc_table:
        for el in doc_table.select('span[id^="Docs_flDocsFileName"]'):
            name = _plain(el)
            if name and len(name) <= 240 and name not in files:
                files.append(name)
            if len(files) >= 100:
                break
    return {
        "source": SOURCE, "source_section": section,
        "purchase_id": purchase_id, "trade_id": code,
        "lot_id": bid_id, "lot_number": lot_number,
        "external_id": f"sber_ast_utp:{bid_id}",
        "title": lot_title[:1000],
        "purchase_title": purchase_title[:1000],
        "trade_type": sale_type,
        "source_status_text": status,
        "property_type": property_type,
        "region": region,
        "cadastral_numbers": cadastres,
        "initial_price_rub": initial, "minimum_price_rub": minimum,
        "deposit_rub": deposit, "area_sqm": area,
        "land_area_sqm": land_area,
        "price_periods": period_rows, "document_names": files,
        **times,
        "current_price_rub": None,
        "coordinates": None,
        "detail_url": canonical,
        "verified_saved_html": True, "verified_live": False,
        "pagination_verified": False, "source_complete": False,
        "ready_for_ingestion": False, "safe_for_archival": False,
    }


def inspect_united_purchase_list(html: str) -> dict:
    """Classify procurement screenshot; do not interpret 44-FZ lots as property."""
    soup = BeautifulSoup(html, "html.parser")
    text = _plain(soup.body)
    has_procurement = "Госзакупки по 44-ФЗ" in text
    property_view_links = [
        a.get("href", "") for a in soup.select('a[href*="/Property/NBT/PurchaseView/"]')
    ]
    return {
        "source": SOURCE, "page_type": "united_procurements" if has_procurement else "unclassified",
        "procurement_44fz_present": has_procurement,
        "visible_property_sale_links": len(property_view_links),
        "can_ingest_property_lots": False,
    }
