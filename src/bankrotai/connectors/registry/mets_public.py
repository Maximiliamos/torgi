"""Read-only, bounded parser of public M-ETS (МЭТС) real-estate sale cards.

Pilot only: catalogue is NOT an exhaustive trade inventory. No DB writes,
Celery scheduling or archival; callers must inspect the extracted evidence.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from bankrotai.connectors.base import AuctionConnector, ConnectorCapabilityError, ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category
from bankrotai.services.property_address_evidence import extract_asset_property_address

if TYPE_CHECKING:
    from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler

BASE = "https://m-ets.ru"
# Official public detail mirror: allow a single exact-identity HTTPS hop only.
# The source/system identifier and normalized canonical URL remain m-ets.ru.
DETAIL_MIRROR = "https://meetings.m-ets.ru"
OFFICIAL_DETAIL_HOSTS = frozenset({"m-ets.ru", "meetings.m-ets.ru"})
# Preserve the eight-category operator-captured property scope, including
# standalone land (11). Any change to scope needs a separately accepted
# before/after archive and source identity audit. Statuses remain (0, 1).
# This is a selected-scope catalogue, never a complete platform inventory.
APARTMENTS_URL = (
    BASE + "/search?q=eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiwzNCwzOSwzNywzOCwyOCw0MSwxMSJ9"
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
    if parsed.scheme != "https" or parsed.netloc not in OFFICIAL_DETAIL_HOSTS or not match:
        raise MetsEvidenceError("Expected an HTTPS M-ETS lot URL with <trade>-<lot> identity")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise MetsEvidenceError("M-ETS lot URL cannot have a query, fragment or credentials")
    trade, lot = match.groups()
    return f"{BASE}/{trade}-{lot}", trade, lot


def detail_request_url(url: str) -> str:
    """Return the preferred public detail origin for a canonical M-ETS lot."""
    canonical, _trade, _lot = _host_lot_url(url)
    return DETAIL_MIRROR + urlsplit(canonical).path


def detail_request_candidates(url: str) -> tuple[str, str]:
    """Return exact-identity detail origins in safe preference order."""
    canonical, _trade, _lot = _host_lot_url(url)
    return DETAIL_MIRROR + urlsplit(canonical).path, canonical


def is_detail_placeholder(html: str) -> bool:
    """Detect the provider's HTTP-200 spinner/interstitial, not a lot page."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('[itemprop="sku"][content], .lot-container') is not None:
        return False
    return soup.select_one(
        ".gorizontal-vertikal img[src^='data:image/gif;base64,']"
    ) is not None


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
    if is_detail_placeholder(html):
        raise MetsEvidenceError("M-ETS detail placeholder/interstitial")
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    expected_sku = f"{trade}-МЭТС-{lot_number}"
    sku_nodes = soup.select('[itemprop="sku"][content]')
    matching_skus = [node for node in sku_nodes if node.get("content") == expected_sku]
    if sku_nodes and len(matching_skus) != 1:
        raise MetsEvidenceError("Page SKU contradicts URL or is ambiguous")
    scope = (
        matching_skus[0].find_parent(class_="lot-container")
        if matching_skus else None
    ) or soup
    text = scope.get_text(" ", strip=True)
    if len(text) < 80:
        raise MetsEvidenceError("M-ETS lot page is empty or script-only")
    # A single exact SKU inside its lot container is independent identity evidence:
    # the provider sometimes renders the human-readable identifier outside it.
    # For legacy/unscoped pages, continue requiring the ID in visible text.
    if expected_sku not in text and (not matching_skus or scope is soup):
        raise MetsEvidenceError("Page has no matching M-ETS trade/lot identifier")

    # Real M-ETS h2.lot-title includes collapsed UI text "еще".
    for collapsed in scope.select("h2.lot-title [ellipse]"):
        collapsed.decompose()
    headings = [
        heading.get_text(" ", strip=True)
        for heading in scope.find_all(["h1", "h2"])
    ]
    title = next(
        (heading for heading in headings if heading and "МЭТС" not in heading
         and not heading.lower().startswith("лот ")), None
    )
    if not title:
        raise MetsEvidenceError("No trustworthy M-ETS lot title in HTML")

    # The first 4k characters of saved HTML are login/crypto dialogs.
    # Read only the primary lot panel so recommended cards cannot overwrite it.
    primary_description = scope.select_one('[itemprop="description"]')
    if primary_description is None and scope is not soup and len(sku_nodes) == 1:
        # Official November 2026 HTML puts the primary detail description
        # OUTSIDE .lot-container, although the verified SKU/title/price/status
        # remain inside. Never borrow a description from another lot or a
        # recommended-card subtree; only one independently identified SKU
        # and exactly one official unscoped description make this safe.
        all_descriptions = soup.select('[itemprop="description"]')
        if len(all_descriptions) == 1:
            candidate = all_descriptions[0]
            if (
                candidate.find_parent(class_="lot-container") is None
                and candidate.find_parent(class_="card-item") is None
            ):
                primary_description = candidate
    description = primary_description.get_text(" ", strip=True) if primary_description else ""
    # Legacy compact HTML may lack schema.org description. Fall back to
    # text only when there are no recommendation cards to misattribute.
    cadastral_scope = title + " " + description
    if not primary_description and not scope.select("a.card-item"):
        cadastral_scope = text[:4000]
    # A single M-ETS lot can contain both land and buildings, each with a
    # distinct cadastre. Retain all *uniquely observed* numbers from the verified
    # main description; cadastral_number remains a legacy representative,
    # never proof that a multi-object lot has only one property.
    cadastral_numbers = list(dict.fromkeys(CADASTRE.findall(cadastral_scope)))
    cadastral = cadastral_numbers[0] if cadastral_numbers else None
    summary_nodes = scope.select(".lot-cost-item")
    initial = None
    current = None
    minimum = None
    for item in summary_nodes:
        classes = item.get("class", [])
        label = item.select_one(".title")
        value = item.select_one(".value")
        if label is None or value is None:
            continue
        label_text = label.get_text(" ", strip=True)
        parsed_price = _price_before_label(
            value.get_text(" ", strip=True) + " " + label_text, label_text
        )
        if label_text == "Начальная цена":
            initial = parsed_price
        elif label_text == "Текущая цена":
            current = parsed_price
        elif label_text == "Минимальная цена":
            minimum = parsed_price
    deposit_node = scope.select_one(".lot-cost-item.zadat")
    deposit = None
    if deposit_node is not None:
        deposit_value = deposit_node.select_one(".value")
        if deposit_value is not None:
            deposit = _price_before_label(
                deposit_value.get_text(" ", strip=True) + " Размер задатка",
                "Размер задатка",
            )
    # Some current provider templates omit the historical CSS class on price
    # blocks. Labels are the contract; never substitute deposit/minimum price.
    if initial is None:
        initial = _price_before_label(text[:5000], "Начальная цена")
    if current is None:
        current = _price_before_label(text[:5000], "Текущая цена")
    if minimum is None:
        minimum = _price_before_label(text[:5000], "Минимальная цена")
    status_node = scope.select_one(".lot-cost-item.status .lot-status-name")
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
                     for x in scope.select(".lot-cost-item.date"))
    date_source = dates or text[:5000]
    deadline = _date_after_label(date_source, "Окончание приема заявок")
    period_deadline = _date_after_label(
        date_source, "Окончание приема заявок на периоде"
    )
    auction_at = (
        _date_after_label(date_source, "Дата торгов")
        or _date_after_label(date_source, "Дата проведения торгов")
        or _date_after_label(date_source, "Подведение итогов")
    )
    category = classify_category(title, (title + " " + description)[:3000] or text[:3000])
    address = extract_asset_property_address(
        title=title,
        description=description,
        cadastral_number=cadastral,
    )

    image_urls: list[str] = []
    image_scope = scope
    for node in image_scope.select("img, a[href], [style*='background-image']"):
        candidates = [
            str(node.get("data-src") or ""),
            str(node.get("data-original") or ""),
            str(node.get("src") or ""),
            str(node.get("href") or ""),
        ]
        candidates.extend(re.findall(
            r"url\(['\"]?([^)'\"]+)", str(node.get("style") or ""), re.I
        ))
        for raw_image in candidates:
            raw_image = raw_image.strip()
            if not raw_image or raw_image.startswith(("data:", "javascript:")):
                continue
            lowered = raw_image.casefold()
            if any(token in lowered for token in ("logo", "icon", "avatar", "captcha", "spinner")):
                continue
            path = urlsplit(raw_image).path.casefold()
            if not (
                re.search(r"\.(?:jpe?g|png|webp)(?:$|\?)", lowered)
                or any(token in path for token in ("/upload", "/photo", "/image", "/file"))
            ):
                continue
            image_url = urljoin(DETAIL_MIRROR + "/", raw_image)
            if image_url not in image_urls:
                image_urls.append(image_url)
    if not image_urls and scope is not soup and len(sku_nodes) == 1:
        # The official page may render its gallery outside .lot-container,
        # just like its primary description.  With one independently verified
        # SKU it is safe to inspect only gallery/photo-shaped nodes outside
        # recommendation cards.
        for node in soup.select(
            ".gallery img, [class*='gallery'] img, [class*='photo'] img, "
            "a[href$='.jpg'], a[href$='.jpeg'], a[href$='.png'], a[href$='.webp']"
        ):
            if node.find_parent(class_="card-item") is not None:
                continue
            raw_image = str(
                node.get("data-src") or node.get("data-original")
                or node.get("src") or node.get("href") or ""
            ).strip()
            if not raw_image:
                continue
            image_url = urljoin(DETAIL_MIRROR + "/", raw_image)
            if image_url not in image_urls:
                image_urls.append(image_url)

    # Keep the canonical provider URL in stored/public evidence. Runtime fetch
    # may use the mirror first, but the mirror is not a trustworthy user-facing
    # detail URL when it serves a generic spinner placeholder.
    public_detail_url = canonical
    return NormalizedLot(
        external_id=f"{trade}-{lot_number}",
        source="m-ets.ru",
        source_system="m-ets.ru",
        title=title[:500],
        description=(description or text[:3000])[:3000],
        category=category,
        region_slug=None,
        region_name=None,
        address=address,
        cadastral_number=cadastral,
        vin=None,
        area=None,
        start_price=initial,
        current_price=current,
        auction_status=status,
        lot_url=public_detail_url,
        source_url=public_detail_url,
        detail_level="public_detail",
        raw_data={
            "source": "official_public_etp",
            "evidence_scope": "pilot_non_exhaustive",
            "cadastral_numbers": cadastral_numbers,
            "cadastral_multi_object": len(cadastral_numbers) > 1,
            "trade_number": f"{trade}-МЭТС",
            "lot_number": int(lot_number),
            "status_evidence": status,
            "period_deadline": period_deadline.isoformat() if period_deadline else None,
            "source_current_price_observed": current is not None,
            "source_initial_price_observed": initial is not None,
            "source_status_text": source_status[:160] if status_node else None,
            "deposit_rub": deposit,
            "minimum_price": minimum,
            "image_url": image_urls[0] if image_urls else None,
            "image_urls": image_urls,
            "mentions_pledge": ("залоге" in description.lower() or "залог" in description.lower()),
            "complete_search_pagination": False,
        },
        application_deadline=deadline,
        auction_at=auction_at,
        auction_timezone="Europe/Moscow",
        next_price_reduction_at=period_deadline,
        procedure_number=f"{trade}-МЭТС",
        platform_name="МЭТС",
    )


class MetsPublicPilot:
    """Never imply all listings were crawled. No S3/PostgreSQL access."""

    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._last_request_at: float | None = None

    def _get(
        self, url: str, *, before_request: Callable[[str], None] | None = None,
    ) -> str:
        # The catalogue stays on m-ets.ru. Detail reads prefer the historical
        # meetings mirror, but November 2026 production evidence shows that the
        # mirror can return the same HTTP-200 spinner placeholder for every lot.
        # In that exact case only, retry the same verified trade/lot identity on
        # canonical m-ets.ru; redirects and identity/schema contradictions still
        # fail closed.
        request_urls = (
            (url,) if url == APARTMENTS_URL else detail_request_candidates(url)
        )
        placeholder_seen = False
        for request_url in request_urls:
            if self._last_request_at is not None:
                remaining = 0.5 - (time.monotonic() - self._last_request_at)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_at = time.monotonic()
            response = None
            for delay in (0.5, 1.0, 2.0, None):
                try:
                    if before_request is not None:
                        before_request(request_url)
                    response = self.session.get(
                        request_url,
                        timeout=(5, 20),
                        allow_redirects=False,
                        headers={
                            "User-Agent": "TORGI-source-quality-pilot/0.1",
                            "Accept": "text/html",
                        },
                    )
                except (
                    requests.Timeout,
                    requests.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                ):
                    if delay is None:
                        raise
                    time.sleep(delay)
                    continue
                if response.status_code in {401, 403, 429}:
                    response.raise_for_status()
                    raise MetsEvidenceError("M-ETS provider access explicitly refused")
                if 500 <= response.status_code <= 504:
                    if delay is None:
                        response.raise_for_status()
                        raise MetsEvidenceError("M-ETS transient HTTP failure persisted")
                    time.sleep(delay)
                    continue
                break
            if response is None:
                raise RuntimeError("M-ETS request retry loop exhausted")
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise MetsEvidenceError("Redirect from public M-ETS page; do not follow")
            if len(response.content) > MAX_HTML_BYTES:
                raise MetsEvidenceError("M-ETS HTML page exceeds safety limit")
            if "text/html" not in response.headers.get("Content-Type", "text/html").lower():
                raise MetsEvidenceError("Expected HTML from M-ETS, not API or binary data")
            if url != APARTMENTS_URL and is_detail_placeholder(response.text):
                placeholder_seen = True
                continue
            return response.text
        if placeholder_seen:
            raise MetsEvidenceError("M-ETS detail placeholder/interstitial")
        raise MetsEvidenceError("M-ETS detail page unavailable")

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
            "category": "selected_real_estate_categories",
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
        self._source_crawler: MetsSourceCrawler | None = None
        self._incremental_detail_cache: dict[str, dict] = {}

    def configure_incremental_detail_cache(self, snapshots: dict[str, dict]) -> None:
        self._incremental_detail_cache = dict(snapshots)
        if self._source_crawler is not None:
            self._source_crawler.set_detail_cache(self._incremental_detail_cache)

    async def prepare_catalogue(self, filters) -> dict | None:
        from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler, MetsSyncFilters
        import asyncio

        if not isinstance(filters, MetsSyncFilters):
            return None
        if self._source_crawler is None:
            self._source_crawler = MetsSourceCrawler(self.pilot)
        return await asyncio.to_thread(self._source_crawler.prepare_catalogue, filters)

    def restore_resume_checkpoint(self, checkpoint: dict) -> str | None:
        from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler
        if self._source_crawler is None:
            self._source_crawler = MetsSourceCrawler(self.pilot)
            self._source_crawler.set_detail_cache(self._incremental_detail_cache)
        return self._source_crawler.restore_checkpoint(checkpoint)

    async def search(self, filters=None, cursor=None) -> ConnectorPage:
        from bankrotai.connectors.registry.mets_sync import (
            MetsSourceCrawler, MetsSyncFilters,
        )
        from bankrotai.connectors.registry.etp_incremental import (
            EtpIncrementalFilters, incremental_mets_search,
        )
        import asyncio

        if isinstance(filters, MetsSyncFilters):
            if self._source_crawler is None:
                self._source_crawler = MetsSourceCrawler(self.pilot)
                self._source_crawler.set_detail_cache(self._incremental_detail_cache)
            return await self._source_crawler.search(filters, cursor)
        if not isinstance(filters, EtpIncrementalFilters) or filters.source_id != self.source_id:
            raise ConnectorCapabilityError(
                "M-ETS only supports explicitly scoped non-archival incremental filters"
            )
        return await asyncio.to_thread(incremental_mets_search, self.pilot, filters, cursor)

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
