"""Read-only RTS-Tender property-sale HTML extraction from saved Angular pages.

Grounded in 28-Oct-2026 public grid /v2/grid/trades/all and /trades/lotCard/<uuid>.
The grid mixes arrested assets, placement rights and other regulatory sections:
property matching alone must NOT label a lot as bankruptcy. Catalogue pagination,
live HTTP access, current status, completeness and DB/Redis/S3 writes are disabled.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

from bs4 import BeautifulSoup

HOST = "it2.rts-tender.ru"
GRID = f"https://{HOST}/v2/grid/trades/all"
LOT_PATH = re.compile(r"^/trades/lotCard/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$")
TRADE_NUMBER = re.compile(r"^\d{12,24}$")
LOT_NUMBER = re.compile(r"^Лот\s+([1-9]\d{0,4})$", re.I)
CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")
PROPERTY = re.compile(
    r"недвижим|земельн|участок|квартир|помещени|здани|гараж|"
    r"жилой дом|машино.?мест|сооружени|доля нежилого",
    re.I,
)
PRICE = re.compile(r"^\s*(\d[\d \u00a0\u202f]*)(?:[.,](\d{1,2}))?\s*(?:₽|руб\.?)\s*$", re.I)


class RtsEvidenceError(ValueError):
    """The source HTML is absent, incompatible or contradictory."""


def _text(element) -> str:
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() if element else ""


def _required(element, selector: str) -> str:
    nodes = element.select(selector)
    if len(nodes) != 1 or not _text(nodes[0]):
        raise RtsEvidenceError(f"RTS required unique field not found: {selector}")
    return _text(nodes[0])


def money(text: str) -> str | None:
    match = PRICE.fullmatch(text)
    if match is None:
        return None
    try:
        value = Decimal(re.sub(r"[\s\u00a0\u202f]", "", match.group(1))
                        + ("." + match.group(2) if match.group(2) else ""))
        return str(value.quantize(Decimal("0.01"))) if value > 0 else None
    except InvalidOperation:
        return None


def checked_lot_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    match = LOT_PATH.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != HOST or match is None
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RtsEvidenceError("Invalid or foreign RTS lot URL")
    lot_uuid = match.group(1)
    try:
        if str(UUID(lot_uuid)) != lot_uuid:
            raise ValueError("noncanonical UUID")
    except ValueError as error:
        raise RtsEvidenceError("Invalid RTS lot UUID") from error
    return f"https://{HOST}/trades/lotCard/{lot_uuid}", lot_uuid


def _notice_url(soup: BeautifulSoup, notice: str) -> str | None:
    links = [
        a for a in soup.select('a[href^="https://torgi.gov.ru/new/public/notices/view/"]')
        if _text(a) == notice
        and a.get("href") == f"https://torgi.gov.ru/new/public/notices/view/{notice}"
    ]
    return links[0]["href"] if len(links) == 1 else None


def parse_grid(html: str, *, limit: int = 100) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div.it-main-grid-card[rts-at="all-trades-lot-card"]')
    if not cards:
        raise RtsEvidenceError("RTS Angular grid has no rendered lot cards")
    accepted, rejected, seen = [], {}, set()
    for card in cards:
        try:
            notice = _required(card, '[rts-at="lot-card-trade-number"]')
            lot_label = _required(card, '[rts-at="lot-card-lot-number"]')
            lot_title = _required(card, '[rts-at="lot-card-lot-name"]')
            regulation = _required(card, '[rts-at="lot-card-procurement-regulation"]')
            match = LOT_NUMBER.fullmatch(lot_label)
            if not TRADE_NUMBER.fullmatch(notice) or match is None:
                raise RtsEvidenceError("Invalid notice or lot number")
            lot_link = card.select_one('[rts-at="lot-card-lot-name"][href]')
            if lot_link is None:
                raise RtsEvidenceError("No canonical lot link")
            url, uid = checked_lot_url(lot_link["href"])
            other = card.select_one('[rts-at="lot-card-lot-number"][href]')
            if other is None or checked_lot_url(other["href"])[1] != uid:
                raise RtsEvidenceError("Lot name and lot number link disagreement")
            if not PROPERTY.search(lot_title):
                rejected["non_property"] = rejected.get("non_property", 0) + 1
                continue
            if uid in seen:
                rejected["duplicate_uuid"] = rejected.get("duplicate_uuid", 0) + 1
                continue
            seen.add(uid)
            initial = card.select_one(".it-main-grid-card__additional-section .rts-text_h3")
            price = money(_text(initial)) if initial else None
            status = _text(card.select_one('[rts-at="lot-card-lot-state"]'))
            if not price or not status:
                raise RtsEvidenceError("Missing RTS card price or status")
            region_node = card.select_one(".it-main-grid-card__main-section p.rts-text_body-m.rts-text_color-secondary")
            region = _text(region_node) if region_node else None
            row = {
                "source": "rts-tender.ru", "notice_number": notice,
                "lot_number": match.group(1), "lot_uuid": uid,
                "external_id": f"rts_property:{uid}",
                "title": lot_title[:700], "detail_url": url,
                "regulation": regulation, "region_hint": region,
                "source_status_text": status,
                "listing_price_rub": price,
                "cadastral_numbers": list(dict.fromkeys(CADASTRE.findall(lot_title))),
                "notice_url": f"https://torgi.gov.ru/new/public/notices/view/{notice}",
                "notice_link_verified_in_detail": False,
                "verified_detail": False, "ready_for_ingestion": False,
            }
            accepted.append(row)
        except RtsEvidenceError:
            rejected["invalid_card"] = rejected.get("invalid_card", 0) + 1
        if len(accepted) >= limit:
            break
    return {
        "source": "rts-tender.ru", "observed_cards": len(cards),
        "property_candidates": accepted, "rejected": rejected,
        "reported_total_label": _text(soup.select_one("app-all-trades-page"))
        .split("Найдено:", 1)[-1].split("лотов", 1)[0].strip()[:80]
        if "Найдено:" in _text(soup.select_one("app-all-trades-page")) else None,
        "pagination_verified": False, "complete_source_inventory": False,
        "safe_for_archival": False,
    }


def parse_lot_detail(html: str, url: str) -> dict:
    canonical, uid = checked_lot_url(url)
    soup = BeautifulSoup(html, "html.parser")
    lot_number = _required(soup, '[data-type-id="lotCardDescriptionLotNumber"]')
    lot_name = _required(soup, '[data-type-id="lotCardDescriptionLotName"]')
    state = _required(soup, '[data-type-id="lotCardDescriptionLotStatus"]')
    price = money(_required(soup, '[data-type-id="lotCardDescriptionLotPrice"]'))
    category = _required(soup, ".lot-card__category-content")
    description = _required(soup, '[data-type-id="lotCardDescriptionPropertyDescription"]')
    notice = _required(soup, '[data-type-id="procedureCardNumberLink"]')
    kind = _required(soup, '[data-type-id="procedureCardProcedureType"]')
    if not TRADE_NUMBER.fullmatch(notice) or not price:
        raise RtsEvidenceError("Missing canonical notice or starting price")
    detail_link = soup.select_one('[data-type-id="lotCardDescriptionLotNumber"][href]')
    if detail_link is None or checked_lot_url(detail_link["href"])[1] != uid:
        raise RtsEvidenceError("RTS detail UUID contradicts URL")
    if not lot_number.startswith(notice) or not lot_number[len(notice):].isdigit():
        raise RtsEvidenceError("RTS detail lot code contradicts notice number")
    ordinal = int(lot_number[len(notice):])
    if ordinal <= 0 or not PROPERTY.search(lot_name + " " + category):
        raise RtsEvidenceError("Missing lot number or not a property lot")
    if _notice_url(soup, notice) is None:
        raise RtsEvidenceError("RTS official notice number/link mismatch")
    date = _text(soup.select_one('[data-type-id="countdownDate"]'))
    return {
        "source": "rts-tender.ru", "notice_number": notice,
        "lot_number": str(ordinal), "source_lot_code": lot_number,
        "lot_uuid": uid, "external_id": f"rts_property:{uid}",
        "title": lot_name[:700], "category": category,
        "source_status_text": state, "regulation": kind,
        "initial_price_rub": price, "current_price_rub": None,
        "description": description[:8000],
        "cadastral_numbers": list(dict.fromkeys(CADASTRE.findall(description + " " + lot_name))),
        "notice_url": _notice_url(soup, notice),
        "application_countdown_date_text": date or None,
        "coordinates": None, "detail_url": canonical,
        "verified_saved_html": True, "verified_live": False,
        "complete_source_inventory": False, "ready_for_ingestion": False,
        "safe_for_archival": False,
    }
