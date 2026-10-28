"""Read-only, bounded parser of public M-ETS (МЭТС) real-estate sale cards.

Pilot only: catalogue is NOT an exhaustive trade inventory. No DB writes,
Celery scheduling or archival; callers must inspect the extracted evidence.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category

BASE = "https://meetings.m-ets.ru"
# M-ETS public search filter for "Квартира" category (36).
APARTMENTS_URL = (
    BASE + "/search?q=eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiJ9&sp=7qr7"
)
DETAIL_PATH = re.compile(r"^/(\d{4,9})-(\d{1,5})/?$")
CADASTRE = re.compile(r"(?<!\d)(\d{2}:\d{1,3}:\d{5,9}:\d{1,9})(?!\d)")
PRICE = re.compile(r"(?<!\w)(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,12})(?:[,\.](\d{1,2}))?\s*₽")
DATE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
MAX_HTML_BYTES = 2_000_000


class MetsEvidenceError(ValueError):
    """Missing or inconsistent public M-ETS evidence; fail closed."""


def _host_lot_url(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    match = DETAIL_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "meetings.m-ets.ru" or not match:
        raise MetsEvidenceError("Expected an HTTPS M-ETS lot URL with <trade>-<lot> identity")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise MetsEvidenceError("M-ETS lot URL cannot have a query, fragment or credentials")
    trade, lot = match.groups()
    return f"{BASE}/{trade}-{lot}", trade, lot


def discover_lot_urls(html: str, *, limit: int = 10) -> list[str]:
    """Extract only canonical detail links from the official public catalogue."""
    if not 1 <= limit <= 50:
        raise ValueError("pilot limit must be 1..50")
    soup = BeautifulSoup(html, "html.parser")
    result: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = urljoin(BASE, str(link["href"]))
        try:
            canonical, _trade, _lot = _host_lot_url(href)
        except MetsEvidenceError:
            continue
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
            if len(result) >= limit:
                break
    return result


def _price_before_label(text: str, label: str) -> float | None:
    for occurrence in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
        # A labelled price should immediately precede the label, not be a
        # deposit or a number deep inside a document/disclosure paragraph.
        preceding = text[max(0, occurrence.start() - 65): occurrence.start()]
        found = list(PRICE.finditer(preceding))
        if found and not preceding[found[-1].end():].strip():
            number = found[-1].group(1)
            cents = found[-1].group(2) or ""
            value = float(re.sub(r"[\s\u00a0\u202f]", "", number) + ("." + cents if cents else ""))
            if value > 0:
                return value
    return None


def _date_after_label(text: str, label: str) -> datetime | None:
    found = re.search(re.escape(label) + r"\s*:?\s*" + DATE.pattern, text, flags=re.IGNORECASE)
    if found is None:
        return None
    try:
        return datetime.strptime(found.group(1) + " " + found.group(2), "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def parse_mets_lot(html: str, url: str) -> NormalizedLot:
    """Build one evidence-backed source lot; never infer terminal status."""
    canonical, trade, lot_number = _host_lot_url(url)
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    text = soup.get_text(" ", strip=True)
    if len(text) < 80:
        raise MetsEvidenceError("M-ETS lot page is empty or script-only")
    if f"{trade}-МЭТС-{lot_number}" not in text and (
        f"Идентификационный номер: {trade}-МЭТС-{lot_number}" not in text
    ):
        raise MetsEvidenceError("Page has no matching M-ETS trade/lot identifier")

    headings = [
        heading.get_text(" ", strip=True)
        for heading in soup.find_all(["h1", "h2"])
    ]
    title = next(
        (heading for heading in headings if heading and "МЭТС" not in heading
         and not heading.lower().startswith("лот ")), None
    )
    if not title:
        raise MetsEvidenceError("No trustworthy M-ETS lot title in HTML")

    cadastral_match = CADASTRE.search(text)
    cadastral = cadastral_match.group(1) if cadastral_match else None
    # The page may contain old, current and initial prices. Capture only
    # values explicitly labelled in the source's summary.
    initial = _price_before_label(text[:4000], "Начальная цена")
    current = _price_before_label(text[:4000], "Текущая цена")
    if "Торги в стадии приема заявок" in text[:4000]:
        status = "active"
    elif "Объявленные торги" in text[:4000]:
        status = "scheduled"
    else:
        status = "unknown"
    deadline = _date_after_label(text[:5000], "Окончание приема заявок")
    # "Окончание приема заявок на периоде" is NOT a full auction deadline.
    period_deadline = _date_after_label(
        text[:5000], "Окончание приема заявок на периоде"
    )
    category = classify_category(title, text[:3000])
    return NormalizedLot(
        external_id=f"{trade}-{lot_number}",
        source="m-ets.ru",
        source_system="m-ets.ru",
        title=title[:500],
        description=text[:3000],
        category=category,
        region_slug=None,
        region_name=None,
        address=None,
        cadastral_number=cadastral,
        vin=None,
        area=None,
        start_price=initial,
        current_price=current,
        auction_status=status,
        lot_url=canonical,
        source_url=canonical,
        detail_level="public_detail",
        raw_data={
            "source": "official_public_etp",
            "evidence_scope": "pilot_non_exhaustive",
            "trade_number": f"{trade}-МЭТС",
            "lot_number": int(lot_number),
            "status_evidence": status,
            "period_deadline": period_deadline.isoformat() if period_deadline else None,
            "source_current_price_observed": current is not None,
            "source_initial_price_observed": initial is not None,
        },
        application_deadline=deadline,
        auction_timezone="Europe/Moscow",
        procedure_number=f"{trade}-МЭТС",
        platform_name="МЭТС",
    )


class MetsPublicPilot:
    """Never imply all listings were crawled. No S3/PostgreSQL access."""

    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._last_request_at: float | None = None

    def _get(self, url: str) -> str:
        if url != APARTMENTS_URL:
            _host_lot_url(url)
        if self._last_request_at is not None:
            remaining = 0.5 - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()
        response = self.session.get(
            url,
            timeout=(5, 25),
            allow_redirects=False,
            headers={"User-Agent": "TORGI-source-quality-pilot/0.1", "Accept": "text/html"},
        )
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise MetsEvidenceError("Redirect from public M-ETS page; do not follow")
        if len(response.content) > MAX_HTML_BYTES:
            raise MetsEvidenceError("M-ETS HTML page exceeds safety limit")
        if "text/html" not in response.headers.get("Content-Type", "text/html").lower():
            raise MetsEvidenceError("Expected HTML from M-ETS, not API or binary data")
        return response.text

    def preview(self, *, max_lots: int = 5) -> dict:
        if not 1 <= max_lots <= 10:
            raise ValueError("bounded M-ETS pilot supports up to 10 sale cards")
        urls = discover_lot_urls(self._get(APARTMENTS_URL), limit=max_lots)
        if not urls:
            raise MetsEvidenceError("No lot hyperlinks in M-ETS catalogue: possible frontend change")
        rows = []
        failures = []
        for url in urls:
            try:
                lot = parse_mets_lot(self._get(url), url)
                rows.append({
                    "external_id": lot.external_id,
                    "title": lot.title,
                    "category": lot.category,
                    "cadastral_number": lot.cadastral_number,
                    "start_price": lot.start_price,
                    "current_price": lot.current_price,
                    "status": lot.auction_status,
                    "application_deadline": (
                        lot.application_deadline.isoformat() if lot.application_deadline else None
                    ),
                    "url": lot.lot_url,
                })
            except (MetsEvidenceError, requests.RequestException, ValueError) as exc:
                # Do not claim successes if the card has an unverified schema.
                failures.append({"url": url, "error_type": type(exc).__name__})
        return {
            "source": "m-ets.ru",
            "category": "apartment",
            "catalogue_url": APARTMENTS_URL,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "written_to_database": False,
            "discovered": len(urls),
            "parsed": len(rows),
            "failed": len(failures),
            "lots": rows,
            "failures": failures,
        }
