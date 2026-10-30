"""Operator-gated CDTRF catalogue→verified-detail connector.

Incremental observation of every official catalogue page (20/50/100 rows).
No missing-lot reconciliation or archive on absent rows: the provider does not
expose a stable snapshot. This module contains NO database writes; the
existing NationwideIngestionService owns persistence and journaling.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass
from math import ceil

import requests

from bankrotai.connectors.base import ConnectorPage
from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.connectors.registry.cdtrf_public import CdtrfEvidenceError
from bankrotai.connectors.registry.etp_incremental import _property_ready, _cdtrf_rejection_code
from bankrotai.services.etp_cdtrf_canary import (
    CdtrfCanaryError, fetch_verified_cdtrf_lot, API, MAX_RESPONSE, REQUEST_TIMEOUT,
)
from bankrotai.services.etp_cdtrf_full_catalogue import (
    CDTRF_PAGE_SIZES, MAX_SCAN_PAGES, CdtrfCatalogueScanError, _url,
)

CURSOR = re.compile(r"^([1-9]\d{0,4}):([0-9a-f]{64})$")
MAX_REJECTED_DETAIL_RATIO = 0.90


def _verified_detail_with_retry(trade_id: str, lot_id: str, session: requests.Session):
    """Retry only transient transport/5xx errors, never identity or 403/429."""
    for attempt in range(3):
        try:
            return fetch_verified_cdtrf_lot(trade_id, lot_id, session=session)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 2:
                raise
        except CdtrfCanaryError as exc:
            if not re.fullmatch(r"CDTRF HTTP (?:502|503|504)", str(exc)) or attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("Unreachable CDTRF detail retry state")


@dataclass(frozen=True, slots=True)
class CdtrfSyncFilters:
    """Explicit operator-only sync; NEVER use in default_source_specs."""
    page_size: int = 100
    max_pages: int = MAX_SCAN_PAGES
    detail_delay_seconds: float = 0.5

    def __post_init__(self):
        if type(self.page_size) is not int or self.page_size not in CDTRF_PAGE_SIZES:
            raise ValueError("CDTRF page_size must be one of 20/50/100")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= MAX_SCAN_PAGES:
            raise ValueError("Unsafe CDTRF max_pages")
        if not 0.5 <= self.detail_delay_seconds <= 10:
            raise ValueError("CDTRF detail_delay_seconds must be 0.5..10")


class CdtrfSourceCrawler:
    """Stateful only within one ingestion run; fail closed on changed pages."""

    def __init__(self, session: requests.Session):
        self.session = session
        self._seen: set[str] = set()
        self._total: int | None = None
        self._expected_page = 1
        self._prior_digest: str | None = None

    def _search(self, filters: CdtrfSyncFilters, cursor: str | None) -> ConnectorPage:
        if cursor is None:
            if self._expected_page != 1:
                raise CdtrfCatalogueScanError("CDTRF restart requires a fresh connector")
            page_no = 1
        else:
            match = CURSOR.fullmatch(cursor)
            if not match or int(match.group(1)) != self._expected_page:
                raise CdtrfCatalogueScanError("Invalid or stale CDTRF cursor")
            if self._prior_digest != match.group(2):
                raise CdtrfCatalogueScanError("CDTRF page proof mismatch")
            page_no = int(match.group(1))
        if page_no > filters.max_pages:
            raise CdtrfCatalogueScanError("CDTRF page budget exceeded")
        response = self.session.get(
            _url(page_no, filters.page_size),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "TORGI-CDTRF-operator-sync/1"},
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
            raise CdtrfCatalogueScanError(f"CDTRF catalogue HTTP {response.status_code} or oversized")
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise CdtrfCatalogueScanError("CDTRF catalogue response is not JSON")
        data = parse_cdtrf_api_page(
            response.json(), page_num=page_no, page_size=filters.page_size,
        )
        total: int = int(data["reported_total"])
        if self._total is None:
            self._total = total
            if ceil(total / filters.page_size) > filters.max_pages:
                raise CdtrfCatalogueScanError("CDTRF catalogue exceeds page budget")
        elif total != self._total:
            raise CdtrfCatalogueScanError("CDTRF total changed during scan")

        expected = min(filters.page_size, max(0, total - (page_no - 1) * filters.page_size))
        if data["observed_rows"] != expected:
            raise CdtrfCatalogueScanError("CDTRF incomplete page / unsupported server PageSize")
        ids = set(data["observed_lot_ids"])
        if ids & self._seen:
            raise CdtrfCatalogueScanError("CDTRF duplicate lot IDs across catalogue pages")
        self._seen.update(ids)

        accepted = []
        rejected: dict[str, int] = {}
        for index, hit in enumerate(data["property_candidates"]):
            if index:
                time.sleep(filters.detail_delay_seconds)
            try:
                lot = _verified_detail_with_retry(
                    str(hit["trade_id"]), str(hit["trade_lot_id"]), self.session
                )
            except (CdtrfCanaryError, CdtrfEvidenceError) as exc:
                # Different records may legitimately lack property details,
                # contain a non-property asset, or have a trade-level HTML
                # page showing a different lot ID. None is evidence that the
                # requested lot is verified; reject just that candidate.
                # The page-wide rejection threshold below fails closed if
                # the provider's detail structure has changed systemically.
                code = _cdtrf_rejection_code(exc)
                if code == "http_or_network_error":
                    # A provider block, 429 or other explicit HTTP refusal
                    # is not a normal missing lot; stop without archival.
                    raise CdtrfCatalogueScanError(
                        "CDTRF detail HTTP failure; no source reconciliation"
                    ) from exc
                rejected[code] = rejected.get(code, 0) + 1
                continue
            except requests.RequestException as exc:
                raise CdtrfCatalogueScanError(
                    "CDTRF detail network failure; no source reconciliation"
                ) from exc
            except ValueError as exc:
                # Never hide an unexpected programming/data error under a
                # generic 'unverified candidate' counter.
                raise CdtrfCatalogueScanError(
                    "CDTRF unexpected detail validation failure"
                ) from exc
            if not (_property_ready(lot) or (
                lot.auction_status == "closed"
                and lot.cadastral_number
                and lot.lot_url
                and lot.start_price is not None
            )):
                rejected["property_not_eligible"] = rejected.get("property_not_eligible", 0) + 1
                continue
            if lot.external_id != hit["external_id"]:
                raise CdtrfCatalogueScanError("CDTRF API/detail lot identity mismatch")
            lot.raw_data = {
                **lot.raw_data,
                "source_scope": "full_selected_catalogue_nonreconciling",
                "complete_source_inventory": False,
                "safe_for_archival": False,
            }
            accepted.append(lot)
        inspected = len(data["property_candidates"])
        if inspected >= 10 and sum(rejected.values()) / inspected > MAX_REJECTED_DETAIL_RATIO:
            raise CdtrfCatalogueScanError(
                "CDTRF detail acceptance collapsed; source may have changed; "
                + ",".join(f"{key}={value}" for key, value in sorted(rejected.items()))
            )

        pages_total = ceil(total / filters.page_size) or 1
        next_no = page_no + 1
        digest = hashlib.sha256(
            "\n".join(sorted(ids)).encode("ascii")
        ).hexdigest()
        self._prior_digest = digest
        self._expected_page = next_no
        next_cursor = f"{next_no}:{digest}" if next_no <= pages_total else None
        return ConnectorPage(
            items=accepted,
            next_cursor=next_cursor,
            metadata={
                "pages_fetched": 1,
                "total_pages": pages_total,
                "progress_current": page_no,
                "progress_total": pages_total,
                "catalogue_lots": data["observed_rows"],
                "property_hint_candidates": inspected,
                "verified_property_lots": len(accepted),
                "details_rejected": sum(rejected.values()),
                "rejection_codes": rejected,
                "requested_category_group": "cdtrf_property_nonreconciling",
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )

    async def search(self, filters: CdtrfSyncFilters, cursor: str | None = None) -> ConnectorPage:
        return await asyncio.to_thread(self._search, filters, cursor)


def operator_sync_spec():
    """An explicit manual source run; never part of default Celery beat."""
    if (os.getenv("CDTRF_OPERATOR_SYNC_ENABLED") != "1"
            or os.getenv("ETP_CANARY_BACKUP_VERIFIED") != "1"
            or os.getenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED") != "1"):
        raise PermissionError("CDTRF sync requires operator authorization and verified backup")
    from bankrotai.services.ingestion import SourceSyncSpec
    return SourceSyncSpec(
        "cdtrf.ru", CdtrfSyncFilters(page_size=100),
        reconcile_missing=False, max_batches=MAX_SCAN_PAGES,
    )
