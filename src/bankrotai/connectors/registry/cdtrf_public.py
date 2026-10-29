"""Read-only CDTRF public HTML/Nuxt parser, grounded in saved 2026-10-28 pages.

Catalog entries use trade IDs; the Nuxt detail has a separate tradeLotId.
Never treat missing catalog entries as deleted or completed auctions.
"""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

BASE = "https://torgi.cdtrf.ru"
DETAIL = re.compile(r"^/trades/([1-9]\d{2,11})/?$")
CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")
DATES = re.compile(r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?)\s*-\s*(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?)")
PROPERTY = re.compile(r"земельн|участок|здани|помещени|квартир|жилой дом|гараж|недвижим|кадастр", re.I)
MAX_BYTES = 2_000_000


class CdtrfEvidenceError(ValueError):
    """Incomplete or contradictory source evidence."""


def _text(element) -> str:
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() if element else ""


def checked_url(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    m = DETAIL.fullmatch(p.path)
    if (p.scheme != "https" or p.netloc != "torgi.cdtrf.ru" or not m
            or p.query or p.fragment or p.username or p.password):
        raise CdtrfEvidenceError("Invalid CDTRF source detail URL")
    trade_id = m.group(1)
    return f"{BASE}/trades/{trade_id}", trade_id


def money(value: str) -> str | None:
    m = re.fullmatch(
        r"\s*(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,12})(?:[.,](\d{1,2}))?\s*(?:₽|руб\.?)?\s*",
        value,
    )
    if not m:
        return None
    try:
        parsed = Decimal(re.sub(r"[\s\u00a0\u202f]", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
        return str(parsed.quantize(Decimal("0.01"))) if parsed > 0 else None
    except InvalidOperation:
        return None


def parse_catalogue(html: str, *, limit: int = 100) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".catalog-card")
    if not cards:
        raise CdtrfEvidenceError("CDTRF public catalog has no catalog-card elements")
    seen: set[str] = set()
    rows = []
    for card in cards[:limit]:
        anchor = card.select_one("a.catalog-card__name[href]")
        code = re.search(r"Код торгов:\s*(\d+)", _text(card.select_one(".catalog-card__code")))
        if anchor is None or code is None:
            continue
        try:
            url, trade_id = checked_url(str(anchor["href"]))
        except CdtrfEvidenceError:
            continue
        if trade_id != code.group(1) or trade_id in seen or not _text(anchor):
            continue
        seen.add(trade_id)
        title = _text(anchor)[:600]
        dt = DATES.search(_text(card.select_one(".catalog-card__trade-info")))
        status = re.sub(r"^Статус:\s*", "", _text(card.select_one(".catalog-card__status")))
        rows.append({
            "source": "cdtrf.ru", "trade_id": trade_id,
            "detail_url": url, "title": title,
            "sale_type": _text(card.select_one(".catalog-card__type")),
            "source_status_text": status,
            "initial_price_rub": money(_text(card.select_one(".catalog-card__price-val"))),
            "application_start": dt.group(1) if dt else None,
            "application_end": dt.group(2) if dt else None,
            "property_hint": bool(PROPERTY.search(title)),
            "trade_lot_id": None,
            "verified_detail": False, "ready_for_ingestion": False,
        })
    navigation = soup.select_one(".pagination__nav")
    pages = [
        int(_text(button)) for button in navigation.select("button.pagination__item")
        if _text(button).isdigit()
    ] if navigation else []
    total = re.search(r"Найдено:\s*([\d \u00a0\u202f]+)\s*лот", _text(soup))
    return {
        "source": "cdtrf.ru", "page_observations": rows,
        "visible_cards": len(cards), "accepted_cards": len(rows),
        "reported_total_lots": int(re.sub(r"\D", "", total.group(1))) if total else None,
        "displayed_last_page": max(pages) if pages else None,
        "pagination_verified": False, "complete_source_inventory": False,
        "safe_for_archival": False,
    }


def _node(nodes: list, index: int, depth: int = 0):
    if not isinstance(index, int) or index < 0 or index >= len(nodes) or depth > 32:
        raise CdtrfEvidenceError("Invalid CDTRF Nuxt reference")
    value = nodes[index]
    if isinstance(value, dict):
        return {key: _node(nodes, ref, depth + 1) for key, ref in value.items()}
    if isinstance(value, list):
        if len(value) == 2 and value[0] in ("Ref", "Reactive", "ShallowReactive"):
            return _node(nodes, value[1], depth + 1)
        return [_node(nodes, ref, depth + 1) for ref in value]
    return value


def _ssr_lot(soup: BeautifulSoup) -> tuple[dict, dict]:
    script = soup.select_one('script#__NUXT_DATA__[type="application/json"]')
    if script is None or len(script.text) > MAX_BYTES:
        raise CdtrfEvidenceError("No CDTRF SSR data")
    try:
        nodes = json.loads(script.text)
        wrapper = nodes[nodes[1]["data"]]
        data = nodes[wrapper[1]] if isinstance(wrapper, list) else wrapper
        for ref in data.values():
            obj = nodes[ref]
            if isinstance(obj, dict) and "lot" in obj and "tradeId" in obj:
                lot = _node(nodes, obj["lot"])
                trade = {
                    key: _node(nodes, obj[key])
                    for key in (
                        "tradeId", "tradeStatus", "tradeStatusDescription",
                        "tradeType", "tradeTypeDescription", "requestTimeBegin",
                        "requestTimeEnd", "winnerTime", "idEFRSB",
                    ) if key in obj
                }
                if isinstance(lot, dict):
                    return lot, trade
    except (TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise CdtrfEvidenceError("Invalid CDTRF SSR structure") from exc
    raise CdtrfEvidenceError("Lot object missing from SSR")


def _lot_property_text(lot: dict) -> str:
    """Extract the actual property description, not the generic auction heading.

    CDTRF publishes it as HTML in the SSR lotInfo field; description is
    optional. Refuse unexpectedly large or non-text values rather than guessing.
    """
    fragments = []
    for key in ("lotInfo", "description"):
        value = lot.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > 16_000:
            raise CdtrfEvidenceError("Invalid CDTRF lot property description")
        fragments.append(value)
    return _text(BeautifulSoup(" ".join(fragments), "html.parser"))


def parse_detail(html: str, url: str) -> dict:
    canonical, trade_id = checked_url(url)
    soup = BeautifulSoup(html, "html.parser")
    lot, trade = _ssr_lot(soup)
    code = re.search(r"Код торгов:\s*(\d+)", _text(soup.select_one(".lot__header")))
    title = _text(soup.select_one(".lot__header h1"))
    if (code is None or code.group(1) != trade_id
            or str(trade.get("tradeId")) != trade_id
            or not title or title != str(lot.get("name", "")).strip()):
        raise CdtrfEvidenceError("Trade ID or title disagreement between URL, HTML and Nuxt")
    lot_id, lot_number = lot.get("tradeLotId"), lot.get("lotNumber")
    if not str(lot_id).isdigit() or not str(lot_number).isdigit():
        raise CdtrfEvidenceError("No stable CDTRF lot identity")
    initial = money(str(lot.get("priceBegin") or ""))
    visible_price = money(_text(soup.select_one(".lot-sidebar__price-val")))
    if not initial or initial != visible_price:
        raise CdtrfEvidenceError("Price disagreement between HTML and SSR")
    region, category = None, None
    for badge in soup.select(".lot__info-badge"):
        label = _text(badge.select_one(".lot__info-badge-label"))
        val = _text(badge.select_one(".lot__info-badge-val"))
        if label == "Категория имущества":
            category = val
        if label == "Адрес местонахождения имущества":
            region = val
    periods = []
    schedule = lot.get("lotScheduleItems") or []
    if not isinstance(schedule, list) or len(schedule) > 300:
        raise CdtrfEvidenceError("Invalid public-offer schedule")
    for interval in schedule:
        if not isinstance(interval, dict):
            raise CdtrfEvidenceError("Malformed price period")
        p = money(str(interval.get("price") or ""))
        if not p or not interval.get("startTime") or not interval.get("endTime"):
            raise CdtrfEvidenceError("Incomplete price period")
        periods.append({
            "start": interval["startTime"], "end": interval["endTime"],
            "price_rub": p,
        })
    raw_cadastre = BeautifulSoup(
        " ".join(str(lot.get(k) or "") for k in ("name", "lotInfo", "description")),
        "html.parser",
    ).get_text(" ", strip=True)
    cadastral = CADASTRE.search(raw_cadastre)
    property_description = _lot_property_text(lot)
    lat, lon = lot.get("latitude"), lot.get("longitude")
    coords = None
    try:
        pair = (float(lat), float(lon)) if lat is not None and lon is not None else (0.0, 0.0)
        if pair != (0.0, 0.0) and -85.0511 <= pair[0] <= 85.0511 and -180 <= pair[1] <= 180:
            coords = {"lat": pair[0], "lon": pair[1]}
    except (TypeError, ValueError):
        pass
    return {
        "source": "cdtrf.ru", "trade_id": trade_id,
        "trade_lot_id": str(lot_id), "lot_number": str(lot_number),
        "external_id": f"cdtrf:{lot_id}",
        "title": title[:600], "category_label": category,
        "property_description": property_description,
        "region_label": region, "region_id": lot.get("regionID"),
        "cadastral_number": cadastral.group(0) if cadastral else None,
        "initial_price_rub": initial, "current_price_rub": None,
        "source_status_code": trade.get("tradeStatus"),
        "source_status_text": trade.get("tradeStatusDescription"),
        "trade_type": trade.get("tradeTypeDescription"),
        "application_start": trade.get("requestTimeBegin"),
        "application_end": trade.get("requestTimeEnd"),
        "result_time": trade.get("winnerTime"),
        "efrsb_message_id": trade.get("idEFRSB"),
        "price_periods": periods, "coordinates": coords,
        "detail_url": canonical,
        "verified_detail": True, "ready_for_ingestion": False,
        "safe_for_archival": False,
    }


class CdtrfPublicPilot:
    def __init__(self, *, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.last_request: float | None = None

    def _get(self, url: str) -> str:
        if url != BASE + "/":
            checked_url(url)
        if self.last_request is not None:
            remaining = 0.5 - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()
        response = self.session.get(
            url, timeout=(5, 25), allow_redirects=False,
            headers={"User-Agent": "TORGI-CDTRF-readonly-pilot/0.1", "Accept": "text/html"},
        )
        if response.status_code in (301, 302, 303, 307, 308, 401, 403, 429):
            raise CdtrfEvidenceError(f"CDTRF HTTP {response.status_code}; stop")
        response.raise_for_status()
        if len(response.content) > MAX_BYTES:
            raise CdtrfEvidenceError("HTML over size limit")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise CdtrfEvidenceError("Not public HTML")
        return response.text

    def preview(self, *, max_lots: int = 5) -> dict:
        if not 1 <= max_lots <= 10:
            raise ValueError("max_lots must be 1..10")
        catalogue = parse_catalogue(self._get(BASE + "/"))
        selected = catalogue.pop("page_observations")[:max_lots]
        details, failures = [], []
        for candidate in selected:
            try:
                details.append(parse_detail(
                    self._get(candidate["detail_url"]), candidate["detail_url"]
                ))
            except (CdtrfEvidenceError, requests.RequestException):
                failures.append({"trade_id": candidate["trade_id"], "error": "detail_unverified"})
        return {
            "catalogue": catalogue, "detail_records": details,
            "failures": failures, "inspected": len(selected),
            "complete_source_inventory": False, "database_writes": 0,
            "safe_for_archival": False,
        }
