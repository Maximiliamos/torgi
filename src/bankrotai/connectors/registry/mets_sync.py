"""Full selected-catalogue M-ETS crawler for non-reconciling ingestion."""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from dataclasses import dataclass
from bankrotai.connectors.registry.detail_transport import DetailWorkers, HostRateLimiter
from decimal import Decimal

import requests

from bankrotai.connectors.base import ConnectorPage
from bankrotai.connectors.registry.etp_incremental import _property_ready
from bankrotai.connectors.registry.mets_public import (
    BASE, MetsEvidenceError, MetsPublicPilot, parse_mets_lot,
)
from bankrotai.services.etp_mets_full_catalogue import (
    MAX_SCAN_PAGES, MetsCatalogueScanError, _parse_page, _request_page,
)

CURSOR = re.compile(r"^([1-9]\d{0,3}):([0-9a-f]{64})$")
MAX_LIVE_TOTAL_DRIFT_RATIO = 0.02


@dataclass(frozen=True, slots=True)
class MetsSyncFilters:
    """Exhaust the proven selected catalogue without missing-lot archival."""

    max_pages: int = MAX_SCAN_PAGES
    detail_delay_seconds: float = 0.5
    detail_concurrency: int = 2
    detail_requests_per_second: float | None = None

    def __post_init__(self) -> None:
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= MAX_SCAN_PAGES:
            raise ValueError("Unsafe M-ETS max_pages")
        if not 0.5 <= self.detail_delay_seconds <= 10:
            raise ValueError("M-ETS detail_delay_seconds must be 0.5..10")
        if type(self.detail_concurrency) is not int or not 1 <= self.detail_concurrency <= 4:
            raise ValueError("Unsafe M-ETS detail_concurrency")
        if self.detail_requests_per_second is not None and not 0 < self.detail_requests_per_second <= 2:
            raise ValueError("M-ETS detail_requests_per_second must be 0..2")


class MetsSourceCrawler:
    """Stateful catalogue/verified-detail walk for one ingestion run."""

    def __init__(self, pilot: MetsPublicPilot) -> None:
        self.pilot = pilot
        self._seen: set[str] = set()
        self._total: int | None = None
        self._minimum_total: int | None = None
        self._maximum_total: int | None = None
        self._page_size: int | None = None
        self._overlap_count = 0
        self._expected_page = 1
        self._prior_digest: str | None = None
        self._detail_limiter: HostRateLimiter | None = None
        self._detail_rate: float | None = None
        self._custom_detail_lock = __import__("threading").Lock()

    def _search(self, filters: MetsSyncFilters, cursor: str | None) -> ConnectorPage:
        if cursor is None:
            if self._expected_page != 1:
                raise MetsCatalogueScanError("M-ETS restart requires a fresh connector")
            page_number = 1
        else:
            match = CURSOR.fullmatch(cursor)
            if not match or int(match.group(1)) != self._expected_page:
                raise MetsCatalogueScanError("Invalid or stale M-ETS cursor")
            if match.group(2) != self._prior_digest:
                raise MetsCatalogueScanError("M-ETS page proof mismatch")
            page_number = int(match.group(1))
        if page_number > filters.max_pages:
            raise MetsCatalogueScanError("M-ETS page budget exceeded")

        response = _request_page(
            self.pilot.session, page_number, sleep=time.sleep,
        )
        parsed = _parse_page(response.text, page_number)
        observations = parsed["observations"]
        identities = [item[0] for item in observations]
        total = int(parsed["total"])
        if self._total is None:
            self._total = total
            self._minimum_total = total
            self._maximum_total = total
            self._page_size = len(identities)
            if not self._page_size:
                raise MetsCatalogueScanError("M-ETS first page is empty")
            if math.ceil(total / self._page_size) > filters.max_pages:
                raise MetsCatalogueScanError("M-ETS catalogue exceeds page budget")
        else:
            assert self._minimum_total is not None
            assert self._maximum_total is not None
            self._minimum_total = min(self._minimum_total, total)
            self._maximum_total = max(self._maximum_total, total)
        assert self._page_size is not None
        assert self._total is not None
        assert self._minimum_total is not None
        assert self._maximum_total is not None
        maximum_drift = max(
            self._page_size * 2,
            math.ceil(self._total * MAX_LIVE_TOTAL_DRIFT_RATIO),
        )
        if self._maximum_total - self._minimum_total > maximum_drift:
            raise MetsCatalogueScanError("M-ETS reported total drift exceeds safe live-catalogue bound")
        pages_total = math.ceil(total / self._page_size)
        source_next_page = parsed.get("next_page")
        if source_next_page is not None and source_next_page != page_number + 1:
            raise MetsCatalogueScanError("M-ETS paginator is not sequential")
        if source_next_page is not None and len(identities) != self._page_size:
            raise MetsCatalogueScanError("M-ETS page size contradicts reported total")
        if source_next_page is None and not 1 <= len(identities) <= self._page_size:
            raise MetsCatalogueScanError("M-ETS terminal page size is invalid")
        overlap = self._seen.intersection(identities)
        if len(overlap) == len(identities):
            raise MetsCatalogueScanError("M-ETS paginator repeated a complete page")
        self._overlap_count += len(overlap)
        if self._overlap_count > maximum_drift:
            raise MetsCatalogueScanError("M-ETS live-catalogue overlap exceeds safe bound")
        novel_observations = [item for item in observations if item[0] not in self._seen]
        self._seen.update(item[0] for item in novel_observations)

        accepted = []
        rejected: dict[str, int] = {}
        rate = filters.detail_requests_per_second or 1.0 / filters.detail_delay_seconds
        if self._detail_limiter is None:
            self._detail_limiter = HostRateLimiter(rate)
            self._detail_rate = rate
        elif self._detail_rate != rate:
            raise MetsCatalogueScanError("M-ETS detail rate changed within one crawl")
        workers = DetailWorkers(
            self.pilot.session, filters.detail_concurrency, rate,
            limiter=self._detail_limiter,
        )

        def verify(item):
            external_id, catalogue_price, catalogue_status, _title = item
            url = f"{BASE}/{external_id}"
            try:
                # Real requests.Session transports are isolated per worker. Custom
                # or injected pilots retain their explicit _get seam for offline tests.
                if (
                    type(self.pilot) is MetsPublicPilot
                    and "_get" not in vars(self.pilot)
                    and isinstance(self.pilot.session, requests.Session)
                ):
                    pilot = getattr(workers._local, "pilot", None)
                    if pilot is None:
                        pilot = MetsPublicPilot(session=workers.session())
                        workers._local.pilot = pilot
                    html = pilot._get(url, before_request=workers.limiter.acquire)
                else:
                    # Unknown/custom transports are not assumed thread-safe.
                    with self._custom_detail_lock:
                        workers.limiter.acquire(url)
                        html = self.pilot._get(url)
                lot = parse_mets_lot(html, url)
                if lot.external_id != external_id:
                    raise MetsEvidenceError("M-ETS detail identity mismatch")
                expected_status = (
                    "active" if catalogue_status == "Торги в стадии приема заявок"
                    else "scheduled"
                )
                if lot.auction_status != expected_status:
                    raise MetsEvidenceError("M-ETS detail status differs from catalogue")
                detail_prices = {
                    Decimal(str(value))
                    for value in (lot.start_price, lot.current_price)
                    if value is not None
                }
                if Decimal(catalogue_price) not in detail_prices:
                    raise MetsEvidenceError("M-ETS detail price differs from catalogue")
                if not _property_ready(lot):
                    return None, "property_evidence_missing"
            except requests.RequestException as exc:
                raise MetsCatalogueScanError(
                    "M-ETS detail network failure; no source reconciliation"
                ) from exc
            except MetsEvidenceError as exc:
                message = str(exc).casefold()
                # HTTP refusal or provider errors are systemic, not a bad lot.
                if "http " in message or "transient http" in message:
                    raise MetsCatalogueScanError(
                        "M-ETS detail HTTP failure; no source reconciliation"
                    ) from exc
                code = (
                    "identity_mismatch" if "identity" in message
                    else "status_mismatch" if "status" in message
                    else "price_mismatch" if "price" in message
                    else "detail_schema_invalid"
                )
                return None, code
            lot.raw_data = {
                **lot.raw_data,
                "source_scope": "full_selected_catalogue_nonreconciling",
                "catalogue_price_verified": True,
                "catalogue_status_verified": True,
                "complete_source_inventory": False,
                "safe_for_archival": False,
            }
            return lot, None

        # map preserves catalogue ordering and propagates any fatal failure.
        # Only details overlap; the next catalogue page is never fetched early.
        with workers.pool() as executor:
            for lot, code in workers.map_ordered(executor, verify, novel_observations):
                if code:
                    rejected[code] = rejected.get(code, 0) + 1
                elif lot is not None:
                    accepted.append(lot)

        digest = hashlib.sha256("\n".join(sorted(identities)).encode("ascii")).hexdigest()
        next_page = page_number + 1
        self._prior_digest = digest
        self._expected_page = next_page
        next_cursor = f"{next_page}:{digest}" if source_next_page is not None else None
        return ConnectorPage(
            items=accepted,
            next_cursor=next_cursor,
            metadata={
                "pages_fetched": 1,
                "total_pages": pages_total,
                "progress_current": page_number,
                "progress_total": pages_total,
                "catalogue_lots": len(novel_observations),
                "catalogue_page_lots": len(identities),
                "live_total_initial": self._total,
                "live_total_minimum": self._minimum_total,
                "live_total_maximum": self._maximum_total,
                "cross_page_overlap": self._overlap_count,
                "verified_property_lots": len(accepted),
                "details_rejected": sum(rejected.values()),
                "detail_attempts": len(novel_observations),
                "detail_concurrency": filters.detail_concurrency,
                "rejection_codes": rejected,
                "requested_category_group": "mets_property_nonreconciling",
                "complete_selected_filter_inventory": next_cursor is None,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )

    async def search(
        self, filters: MetsSyncFilters, cursor: str | None = None,
    ) -> ConnectorPage:
        return await asyncio.to_thread(self._search, filters, cursor)
