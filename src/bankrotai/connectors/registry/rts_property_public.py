"""Conservative parser for the public RTS property-trading grid."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


BASE = "https://it2.rts-tender.ru/v2/grid/trades/all"
LOT_PATH = re.compile(
    r"^/v2/trades/(?P<trade>[0-9a-f-]{36})/lots/(?P<lot>[0-9a-f-]{36})$",
    re.IGNORECASE,
)
PRICE = re.compile(r"(?<!\d)(\d[\d\s\u00a0]*)[,.](\d{2})\s*₽")
NOTICE = re.compile(r"\b(?:COM\d{8,}|B\d{8,}|\d{8,})\b")


class RtsGridEvidenceError(ValueError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _price(value: str) -> float | None:
    matches = list(PRICE.finditer(value))
    if not matches:
        return None
    # In the live card the first RUB amount is the lot price; later amounts
    # are deposit and tariff.  Zero is not published as a verified price.
    raw = re.sub(r"[\s\u00a0]", "", matches[0].group(1)) + "." + matches[0].group(2)
    amount = float(raw)
    return amount if 0 < amount < 1e15 else None


def parse_grid(html: str | bytes, *, limit: int = 30) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.select('a[href*="/v2/trades/"][href*="/lots/"]'):
        absolute = urljoin(BASE, str(anchor.get("href") or ""))
        parsed = urlsplit(absolute)
        match = LOT_PATH.fullmatch(parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "it2.rts-tender.ru" or not match:
            continue
        external_id = f"rts-tender.ru:{match.group('trade')}:{match.group('lot')}"
        if external_id in found:
            continue
        row = anchor.find_parent("tr") or anchor.find_parent(class_=re.compile(r"card|item|row", re.I))
        context = _clean((row or anchor.parent or anchor).get_text(" ", strip=True))
        title = _clean(anchor.get_text(" ", strip=True))
        if not title or re.fullmatch(r"Лот\s*\d+", title, re.I):
            sibling = (row or anchor.parent or anchor).select_one(
                f'a[href="{anchor.get("href")}"][id^="lotDescription_"]'
            ) if hasattr((row or anchor.parent or anchor), "select_one") else None
            if sibling is not None:
                title = _clean(sibling.get_text(" ", strip=True))
        if not title or re.fullmatch(r"Лот\s*\d+", title, re.I):
            continue
        notice_match = NOTICE.search(context)
        lot_number = None
        lot_match = re.search(r"\bЛот\s*(\d+)\b", context, re.I)
        if lot_match:
            lot_number = lot_match.group(1)
        status = next((value for value in (
            "приём заявок", "опубликован", "торги", "завершен", "отменен"
        ) if value in context.lower()), "unknown")
        found[external_id] = {
            "external_id": external_id,
            "notice_number": notice_match.group(0) if notice_match else match.group("trade"),
            "lot_number": lot_number or "1",
            "title": title,
            "detail_url": absolute,
            "listing_price_rub": _price(context),
            "source_status_text": status,
            "regulation": None,
            "notice_url": None,
        }
        if len(found) >= limit:
            break
    if not found:
        raise RtsGridEvidenceError("RTS public grid contains no stable lot cards")
    return {"source": "rts-tender.ru", "property_candidates": list(found.values())}
