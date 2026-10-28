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

from bankrotai.connectors.base import AuctionConnector, ConnectorCapabilityError, ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category

BASE = "https://m-ets.ru"
# User-observed search filter for seven real-estate category IDs; full paging unverified.
APARTMENTS_URL = (
    BASE + "/search?q=eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiwzNCwzOSwzNywzOCwyOCw0MSJ9"
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
    if parsed.scheme != "https" or parsed.netloc != "m-ets.ru" or not match:
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
    identity = soup.select_one('meta[property="og:url"]')
    if identity is not None and identity.get("content") == BASE + "/":
        raise MetsEvidenceError("Homepage is not a verified M-ETS search catalogue")
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

    # Real M-ETS h2.lot-title includes collapsed UI text "еще".
    for collapsed in soup.select("h2.lot-title [ellipse]"):
        collapsed.decompose()
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

    # The first 4k characters of saved HTML are login/crypto dialogs.
    # Read only the primary lot panel so recommended cards cannot overwrite it.
    primary_description = soup.select_one('[itemprop="description"]')
    description = primary_description.get_text(" ", strip=True) if primary_description else ""
    cadastral_match = CADASTRE.search(title + " " + description)
    cadastral = cadastral_match.group(1) if cadastral_match else None
    meta_sku = soup.select_one('[itemprop="sku"][content]')
    if meta_sku is not None and meta_sku.get("content") != f"{trade}-МЭТС-{lot_number}":
        raise MetsEvidenceError("M-ETS page SKU contradicts URL")
    summary_nodes = soup.select(".lot-cost-item")
    initial = None
    current = None
    for item in summary_nodes:
        classes = item.get("class", [])
        label = item.select_one(".title")
        value = item.select_one(".value")
        if label is None or value is None or "price" not in classes:
            continue
        label_text = label.get_text(" ", strip=True)
        parsed_price = _price_before_label(
            value.get_text(" ", strip=True) + " " + label_text, label_text
        )
        if label_text == "Начальная цена":
            initial = parsed_price
        elif label_text == "Текущая цена":
            current = parsed_price
    deposit_node = soup.select_one(".lot-cost-item.zadat")
    deposit = None
    if deposit_node is not None:
        deposit_value = deposit_node.select_one(".value")
        if deposit_value is not None:
            deposit = _price_before_label(
                deposit_value.get_text(" ", strip=True) + " Размер задатка",
                "Размер задатка",
            )
    if not summary_nodes:  # Compatibility with earlier minimal mocked HTML.
        initial = _price_before_label(text[:4000], "Начальная цена")
        current = _price_before_label(text[:4000], "Текущая цена")
    status_node = soup.select_one(".lot-cost-item.status .lot-status-name")
    source_status = (
        status_node.get_text(" ", strip=True) if status_node
        else text[:4000]
    )
    if "Торги в стадии приема заявок" in source_status:
        status = "active"
    elif "Объявленные торги" in source_status:
        status = "scheduled"
    else:
        status = "unknown"
    dates = " ".join(x.get_text(" ", strip=True)
                     for x in soup.select(".lot-cost-item.date"))
    date_source = dates or text[:5000]
    deadline = _date_after_label(date_source, "Окончание приема заявок")
    period_deadline = _date_after_label(
        date_source, "Окончание приема заявок на периоде"
    )
    category = classify_category(title, (title + " " + description)[:3000] or text[:3000])
    return NormalizedLot(
        external_id=f"{trade}-{lot_number}",
        source="m-ets.ru",
        source_system="m-ets.ru",
        title=title[:500],
        description=(description or text[:3000])[:3000],
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
            "source_status_text": source_status[:160] if status_node else None,
            "deposit_rub": deposit,
            "mentions_pledge": ("залоге" in description.lower() or "залог" in description.lower()),
            "complete_search_pagination": False,
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


class MetsConnector(AuctionConnector):
    """Explicit connector registry entry; ingestion search is locked until full crawl."""

    source_id = "m-ets.ru"
    capabilities = frozenset({"fetch_lot", "read_only_pilot"})

    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.pilot = MetsPublicPilot(session=session)

    async def search(self, filters=None, cursor=None) -> ConnectorPage:
        raise ConnectorCapabilityError(
            "M-ETS supports read-only preview and exact detail only; "
            "no archive-safe full catalogue pagination has been verified"
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        import asyncio

        if not re.fullmatch(r"\d{4,9}-\d{1,5}", external_id):
            raise MetsEvidenceError("M-ETS external_id must be <trade>-<lot>")
        url = f"{BASE}/{external_id}"
        html = await asyncio.to_thread(self.pilot._get, url)
        return parse_mets_lot(html, url)

    async def preview(self, *, max_lots: int = 5) -> dict:
        import asyncio

        return await asyncio.to_thread(self.pilot.preview, max_lots=max_lots)
