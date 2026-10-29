"""Limited, opt-in real-estate ingestion probes for M-ETS and CDTRF.

Grounded in the user's 29-Oct browser capture: M-ETS /search page=2
and CDTRF GET /api/Trade/trades PageNum=1/2. These are *incremental
windows*, never evidence of complete coverage. Caller MUST set
SourceSyncSpec.reconcile_missing=False.

No CAPTCHA/anti-DDoS bypass or automated retry for 401/403/429.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

import requests

from bankrotai.connectors.base import ConnectorPage
from bankrotai.connectors.registry.mets_public import (
    APARTMENTS_URL, MAX_HTML_BYTES, MetsEvidenceError, MetsPublicPilot,
    discover_lot_urls, parse_mets_lot,
)
from bankrotai.services.etp_cdtrf_canary import (
    CdtrfCanaryError, fetch_verified_cdtrf_lot, preview_cdtrf_public_api,
)

CANARY_SOURCES = frozenset({"m-ets.ru", "cdtrf.ru"})
LOT_ID = re.compile(r"^[1-9]\d{3,8}-[1-9]\d{0,4}$")
METS_PAGE = re.compile(r"^[2-5]:[0-9a-f]{16}$")
PROPERTY_CATEGORIES = frozenset({
    "apartment", "house", "land", "parking", "commercial_room",
    "commercial_building", "commercial_building_with_land",
    "unfinished", "complex", "real_estate",
})


@dataclass(frozen=True, slots=True)
class EtpIncrementalFilters:
    """A strictly bounded discovery window; not an exhaustive source spec."""
    source_id: str
    max_detail_requests: int = 3
    max_pages: int = 2

    def __post_init__(self) -> None:
        if self.source_id not in CANARY_SOURCES:
            raise ValueError("Source not enabled for bounded ETP ingestion")
        if not 1 <= self.max_detail_requests <= 5:
            raise ValueError("ETP detail request cap must be 1..5")
        if not 1 <= self.max_pages <= 5:
            raise ValueError("ETP incremental window must be bounded to 1..5 pages")


def _cursor_for(ids: list[str], next_page: int = 2) -> str:
    if not 2 <= next_page <= 5:
        raise ValueError("ETP cursor page exceeds the bounded five-page window")
    payload = ",".join(sorted(ids)).encode("ascii")
    return f"{next_page}:" + hashlib.sha256(payload).hexdigest()[:16]


def _validate_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 1
    if not METS_PAGE.fullmatch(cursor):
        raise ValueError("Untrusted ETP page cursor")
    return int(cursor.split(":", 1)[0])


def _fingerprints(ids: list[str]) -> list[str]:
    """Full SHA-256 public lot-ID fingerprints for cross-page evidence.

    Return no raw source identifiers in the bounded audit metadata. This
    intentionally represents all observed *candidate* IDs, not only the
    subset whose detail requests were authorized.
    """
    if len(ids) != len(set(ids)):
        raise ValueError("ETP catalogue identities are duplicated within a page")
    return [hashlib.sha256(x.encode("utf-8")).hexdigest() for x in ids]


def _bounded_scope(pages: int) -> str:
    if pages == 2:
        return "two_page_incremental_window"
    return f"bounded_{pages}_page_incremental_window"


def _property_ready(lot) -> bool:
    return bool(
        lot.category in PROPERTY_CATEGORIES
        and lot.cadastral_number
        and (lot.start_price is not None or lot.current_price is not None)
        and lot.auction_status in {"scheduled", "active"}
        and lot.lot_url
    )


def _get_mets_catalogue(pilot: MetsPublicPilot, page: int) -> str:
    if page == 1:
        return pilot._get(APARTMENTS_URL)
    if not 2 <= page <= 5:
        raise MetsEvidenceError("Only five M-ETS catalogue pages are approved for this window")
    url = APARTMENTS_URL + f"&page={page}"
    if pilot._last_request_at is not None:
        delay = 0.5 - (time.monotonic() - pilot._last_request_at)
        if delay > 0:
            time.sleep(delay)
    pilot._last_request_at = time.monotonic()
    response = pilot.session.get(
        url, timeout=(5, 25), allow_redirects=False,
        headers={"User-Agent": "TORGI-source-quality-pilot/0.1", "Accept": "text/html"},
    )
    if response.status_code != 200:
        raise MetsEvidenceError(f"M-ETS catalogue HTTP {response.status_code}")
    if (len(response.content) > MAX_HTML_BYTES
            or "text/html" not in response.headers.get("Content-Type", "").lower()):
        raise MetsEvidenceError("M-ETS catalogue page response is not bounded HTML")
    return response.text


def incremental_mets_search(
    pilot: MetsPublicPilot, filters: EtpIncrementalFilters, cursor: str | None,
) -> ConnectorPage:
    if filters.source_id != "m-ets.ru":
        raise ValueError("M-ETS incremental filters require source_id=m-ets.ru")
    page = _validate_cursor(cursor)
    if page > filters.max_pages:
        raise ValueError("M-ETS requested page exceeds the approved bounded window")
    html = _get_mets_catalogue(pilot, page)
    urls = discover_lot_urls(html, limit=50)
    if not urls:
        raise MetsEvidenceError("No identifiable sale lots; refusing empty ETP sync")
    ids = [x.rsplit("/", 1)[-1] for x in urls]
    if len(ids) != len(set(ids)) or not all(LOT_ID.fullmatch(x) for x in ids):
        raise MetsEvidenceError("M-ETS catalogue contains ambiguous lot IDs")
    if page > 1 and cursor == _cursor_for(ids, page):
        raise MetsEvidenceError("M-ETS catalogue repeats the immediately preceding page")
    accepted = []
    rejected = 0
    for url in urls[:filters.max_detail_requests]:
        try:
            lot = parse_mets_lot(pilot._get(url), url)
            if not _property_ready(lot):
                rejected += 1
                continue
            lot.raw_data = {**lot.raw_data,
                            "source_scope": _bounded_scope(filters.max_pages),
                            "complete_source_inventory": False}
            accepted.append(lot)
        except (requests.RequestException, MetsEvidenceError, ValueError):
            rejected += 1
    return ConnectorPage(
        items=accepted,
        next_cursor=_cursor_for(ids, page + 1) if page < filters.max_pages else None,
        metadata={
            "pages_fetched": 1, "progress_current": page,
            "progress_total": filters.max_pages,
            "catalogue_lots": len(ids), "details_rejected": rejected,
            "catalogue_identity_fingerprints": _fingerprints(ids),
            "complete_source_inventory": False, "safe_for_archival": False,
            "requested_category_group": "property_pilot",
        },
    )


def _cdtrf_rejection_code(exc: Exception) -> str:
    """Only deterministic failure categories. Never publish source HTML or PII."""
    msg = str(exc).lower()
    if "lot id differs" in msg or "trade id or title disagreement" in msg:
        return "lot_identity_mismatch"
    if "no stable cdtrf lot identity" in msg:
        return "missing_lot_identity"
    if "price disagreement" in msg:
        return "price_mismatch"
    if "no cdtrf ssr" in msg or "invalid cdtrf ssr" in msg or "lot object missing" in msg:
        return "ssr_schema_changed"
    for marker, code in (
        ("cdtrf_unsupported_property_right", "unsupported_property_right"),
        ("cdtrf_category_unverified", "category_unverified"),
        ("cdtrf_cadastral_missing", "cadastral_missing"),
        ("cdtrf_price_missing", "price_missing"),
        ("cdtrf_status_unrecognized", "status_unrecognized"),
    ):
        if marker in msg:
            return code
    if "insufficient property" in msg:
        return "property_evidence_missing"
    if "http " in msg or isinstance(exc, requests.RequestException):
        return "http_or_network_error"
    return "other_detail_error"


def incremental_cdtrf_search(
    filters: EtpIncrementalFilters, cursor: str | None,
    *, session: requests.Session | None = None,
) -> ConnectorPage:
    if filters.source_id != "cdtrf.ru":
        raise ValueError("CDTRF incremental filters require source_id=cdtrf.ru")
    page = _validate_cursor(cursor)
    if page > filters.max_pages:
        raise ValueError("CDTRF requested page exceeds the approved bounded window")
    own = session or requests.Session()
    data = preview_cdtrf_public_api(page, session=own)
    candidates = data["candidates"]
    identities = [str(x["trade_lot_id"]) for x in candidates]
    if not identities:
        raise CdtrfCanaryError("No property candidates; refusing misleading ETP page")
    if len(identities) != len(set(identities)):
        raise CdtrfCanaryError("Repeated CDTRF property identities on page")
    if page > 1 and cursor == _cursor_for(identities, page):
        raise CdtrfCanaryError("CDTRF API repeats the immediately preceding page")
    accepted = []
    rejected = 0
    rejected_codes: dict[str, int] = {}
    for hit in candidates[:filters.max_detail_requests]:
        try:
            lot = fetch_verified_cdtrf_lot(
                str(hit["trade_id"]), str(hit["trade_lot_id"]), session=own
            )
            if not _property_ready(lot):
                rejected += 1
                rejected_codes["property_evidence_missing"] = (
                    rejected_codes.get("property_evidence_missing", 0) + 1
                )
                continue
            lot.raw_data = {**lot.raw_data,
                            "source_scope": _bounded_scope(filters.max_pages),
                            "complete_source_inventory": False}
            accepted.append(lot)
        except (requests.RequestException, CdtrfCanaryError, ValueError) as exc:
            rejected += 1
            code = _cdtrf_rejection_code(exc)
            rejected_codes[code] = rejected_codes.get(code, 0) + 1
    return ConnectorPage(
        items=accepted,
        next_cursor=_cursor_for(identities, page + 1) if page < filters.max_pages else None,
        metadata={
            "pages_fetched": 1, "progress_current": page,
            "progress_total": filters.max_pages,
            "catalogue_lots": len(identities), "details_rejected": rejected,
            "catalogue_identity_fingerprints": _fingerprints(identities),
            "detail_attempts": min(len(candidates), filters.max_detail_requests),
            "rejection_codes": rejected_codes,
            "complete_source_inventory": False, "safe_for_archival": False,
            "requested_category_group": "property_pilot",
        },
    )
