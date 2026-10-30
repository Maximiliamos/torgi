"""Administrator-only ETP preview map, isolated from production lot ingestion.

Preview records are volatile and unverified; no database sessions, source sync,
archival, journal, S3 production map or reconciliation is invoked here.
A preview never certifies an auction. All URLs are validated by source adapters.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from bankrotai.connectors.etp_sources import (
    SOURCE_CONNECTOR_TYPES, PROFILES, EtpSearchFilters,
)
from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import (
    EtpIncrementalFilters,
)

PROFILES_BY_ID = {p.source_id: p for p in PROFILES}
SUPPORTED = frozenset(PROFILES_BY_ID)
MAX_PAGES = 2
MAX_LISTED_PER_PAGE = 3
MAX_GEO_LOOKUPS = 3


def _valid_geo_result(result: Any, cadastral: str) -> bool:
    return bool(
        result is not None
        and getattr(result, "cadastral_number", None) == cadastral
        and isinstance(getattr(result, "lat", None), (int, float))
        and isinstance(getattr(result, "lon", None), (int, float))
        and math.isfinite(result.lat) and math.isfinite(result.lon)
        and 40.0 <= result.lat <= 82.0
        and 19.0 <= result.lon <= 180.0
    )


async def _collect(source: str, pages: int, per_page: int) -> tuple[list[Any], int, list[str]]:
    if source in {"m-ets.ru", "cdtrf.ru"}:
        connector = connector_registry.create(source)
        filters: Any = EtpIncrementalFilters(
            source, max_detail_requests=per_page, max_pages=pages
        )
    else:
        connector = SOURCE_CONNECTOR_TYPES[source]()
        filters = EtpSearchFilters(page=1)
    results: list[Any] = []
    seen: set[str] = set()
    errors: list[str] = []
    cursor: str | None = None
    fetched = 0
    for index in range(pages):
        try:
            page = await connector.search(filters, cursor)
            fetched += 1
        except Exception as exc:
            errors.append(type(exc).__name__)
            break
        if page.metadata.get("complete_source_inventory") is not False:
            errors.append("unsafe_inventory_claim")
            break
        for candidate in page.items[:per_page]:
            identity = str(getattr(candidate, "external_id", "") or "").strip()
            if not identity or identity in seen or candidate.source_system != source:
                continue
            seen.add(identity)
            # Generic search is listing-only. Validate per-lot detail where proven.
            lot = candidate
            if source not in {"m-ets.ru", "cdtrf.ru"} and "fetch_lot" in connector.capabilities:
                try:
                    detail = await connector.fetch_lot(identity)
                    if detail.external_id == identity and detail.source_system == source:
                        lot = detail
                except Exception as exc:
                    errors.append("detail_" + type(exc).__name__)
            results.append(lot)
        cursor = page.next_cursor
        if not cursor:
            break
    return results, fetched, errors[:12]


async def preview(source: str, *, pages: int = 2, per_page: int = 3,
                  geo_limit: int = 2) -> dict[str, Any]:
    """Bound live inspection for one source; never change production records."""
    if source not in SUPPORTED:
        raise ValueError("Unknown ETP source")
    if not 1 <= pages <= MAX_PAGES or not 1 <= per_page <= MAX_LISTED_PER_PAGE:
        raise ValueError("Unbounded ETP QA catalogue request")
    if not 0 <= geo_limit <= MAX_GEO_LOOKUPS:
        raise ValueError("Unbounded ETP QA geocoder request")
    profile = PROFILES_BY_ID[source]
    response: dict[str, Any] = {
        "source": source,
        "qa_only": True,
        "verified_for_production": False,
        "complete_source_inventory": False,
        "database_writes": 0,
        "archival_enabled": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "blocked" if profile.blocked_reason else "preview",
        "blocked_reason": profile.blocked_reason,
        "pages_fetched": 0,
        "candidate_count": 0,
        "mapped_count": 0,
        "errors": [],
        "items": [],
    }
    if profile.blocked_reason:
        return response

    lots, fetched, errors = await _collect(source, pages, per_page)
    response["pages_fetched"] = fetched
    response["candidate_count"] = len(lots)
    response["errors"] = errors
    if not lots and errors:
        response["status"] = "unavailable"

    # The same cadastre may appear multiple times: geocode it only once.
    geo_cache: dict[str, tuple[float, float] | None] = {}
    for lot in lots:
        cadastral = str(lot.cadastral_number or "").replace(" ", "").strip()
        coordinate: tuple[float, float] | None = None
        if cadastral and (cadastral in geo_cache or len(geo_cache) < geo_limit):
            if cadastral not in geo_cache:
                from bankrotai.geo import CADASTRAL_GEOCODER

                try:
                    result = await asyncio.to_thread(
                        CADASTRAL_GEOCODER.search_by_cadastral_number, cadastral
                    )
                    geo_cache[cadastral] = (
                        (float(result.lat), float(result.lon))
                        if _valid_geo_result(result, cadastral) else None
                    )
                except Exception:
                    geo_cache[cadastral] = None
            coordinate = geo_cache[cadastral]
        raw_url = str(lot.lot_url or "")
        # The connector already validated link host and HTTPS. Preserve only
        # first-party link; display no arbitrary user-supplied redirects.
        from urllib.parse import urlsplit
        parsed = urlsplit(raw_url)
        trusted_hosts = set(profile.allowed_hosts)
        if parsed.scheme != "https" or parsed.hostname not in trusted_hosts or parsed.port is not None:
            continue
        row = {
            "external_id": str(lot.external_id)[:120],
            "title": str(lot.title)[:300],
            "url": raw_url,
            "category": str(lot.category or "unknown"),
            "cadastral_number": cadastral or None,
            "start_price": lot.start_price,
            "current_price": lot.current_price,
            "status": str(lot.auction_status or "unknown"),
            "detail_level": str(lot.detail_level or "list"),
            "lat": coordinate[0] if coordinate else None,
            "lon": coordinate[1] if coordinate else None,
            "geo_status": "exact_cadastre" if coordinate else (
                "no_cadastral" if not cadastral else "unresolved"
            ),
            "unverified": True,
        }
        response["items"].append(row)
    response["mapped_count"] = sum(x["lat"] is not None for x in response["items"])
    return response
