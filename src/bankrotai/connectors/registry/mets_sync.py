"""Full selected-catalogue M-ETS crawler for non-reconciling ingestion."""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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
from bankrotai.services.incremental_detail_cache import (
    catalogue_fingerprint, snapshot_reusable,
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
        self._custom_detail_lock = threading.Lock()
        self._detail_cache: dict[str, dict] = {}
        self._prepared_pages: dict[int, dict] = {}

    def set_detail_cache(self, snapshots: dict[str, dict]) -> None:
        self._detail_cache = dict(snapshots)

    @staticmethod
    def _catalogue_fingerprint(item) -> str:
        external_id, catalogue_price, catalogue_status, title = item
        return catalogue_fingerprint({
            "external_id": external_id,
            "price": str(catalogue_price),
            "status": catalogue_status,
            "title": " ".join(str(title or "").split()),
        })

    @staticmethod
    def _expected_status(catalogue_status: str) -> str:
        return "active" if catalogue_status == "Торги в стадии приема заявок" else "scheduled"

    def _can_reuse_detail(self, item) -> bool:
        external_id, catalogue_price, catalogue_status, title = item
        return snapshot_reusable(
            self._detail_cache.get(external_id),
            expected_price=catalogue_price,
            expected_status=self._expected_status(catalogue_status),
            expected_title=title,
            fingerprint=self._catalogue_fingerprint(item),
        )

    def _reset_catalogue_state(self) -> None:
        self._seen = set()
        self._total = None
        self._minimum_total = None
        self._maximum_total = None
        self._page_size = None
        self._overlap_count = 0
        self._expected_page = 1
        self._prior_digest = None

    @staticmethod
    def _ids(parsed: dict) -> list[str]:
        return [item[0] for item in parsed["observations"]]

    def prepare_catalogue(self, filters: MetsSyncFilters) -> dict:
        """Materialize and validate the selected catalogue before any detail request."""
        if self._seen or self._expected_page != 1:
            raise MetsCatalogueScanError("M-ETS catalogue preparation requires a fresh crawler")
        prepared: dict[int, dict] = {}
        seen: set[str] = set()
        first_size: int | None = None
        first_total: int | None = None
        minimum_total: int | None = None
        maximum_total: int | None = None
        overlap_count = 0
        for page_number in range(1, filters.max_pages + 1):
            response = _request_page(self.pilot.session, page_number, sleep=time.sleep)
            parsed = _parse_page(response.text, page_number)
            identities = self._ids(parsed)
            total = int(parsed["total"])
            if first_size is None:
                first_size, first_total = len(identities), total
                minimum_total = maximum_total = total
                if not first_size or math.ceil(total / first_size) > filters.max_pages:
                    raise MetsCatalogueScanError("M-ETS catalogue exceeds page budget")
            else:
                assert first_total is not None and first_size is not None
                assert minimum_total is not None and maximum_total is not None
                minimum_total, maximum_total = min(minimum_total, total), max(maximum_total, total)
            assert first_size is not None and first_total is not None
            assert minimum_total is not None and maximum_total is not None
            maximum_drift = max(first_size * 2, math.ceil(first_total * MAX_LIVE_TOTAL_DRIFT_RATIO))
            if maximum_total - minimum_total > maximum_drift:
                raise MetsCatalogueScanError("M-ETS reported total drift exceeds safe live-catalogue bound")
            next_page = parsed.get("next_page")
            expected_last = math.ceil(total / first_size)
            if next_page is None and page_number < expected_last:
                next_page = page_number + 1
                parsed = {**parsed, "next_page": next_page}
            if next_page is not None and next_page != page_number + 1:
                raise MetsCatalogueScanError("M-ETS paginator is not sequential")
            if next_page is not None and len(identities) != first_size:
                raise MetsCatalogueScanError("M-ETS page size contradicts reported total")
            if next_page is None:
                if page_number != expected_last or not 1 <= len(identities) <= first_size:
                    raise MetsCatalogueScanError("M-ETS terminal page contradicts reported total")
            overlap = seen.intersection(identities)
            if len(overlap) == len(identities):
                raise MetsCatalogueScanError("M-ETS paginator repeated a complete page")
            overlap_count += len(overlap)
            if overlap_count > maximum_drift:
                raise MetsCatalogueScanError("M-ETS live-catalogue overlap exceeds safe bound")
            seen.update(identities)
            prepared[page_number] = parsed
            if next_page is None:
                if len(seen) < max(1, minimum_total - maximum_drift):
                    raise MetsCatalogueScanError(
                        "M-ETS catalogue ended below reported unique coverage"
                    )
                self._prepared_pages = prepared
                self._reset_catalogue_state()
                return {
                    "pages": page_number,
                    "observed_unique_ids": len(seen),
                    "reported_total": total,
                    "phase": "catalogue_discovery_complete",
                }
        raise MetsCatalogueScanError("M-ETS page budget exhausted during catalogue discovery")

    def _parsed_page(self, page_number: int) -> dict:
        prepared = self._prepared_pages.get(page_number)
        if prepared is not None:
            return prepared
        response = _request_page(self.pilot.session, page_number, sleep=time.sleep)
        return _parse_page(response.text, page_number)

    def restore_checkpoint(self, checkpoint: dict) -> str | None:
        if checkpoint.get("version") != 1 or checkpoint.get("source") != "m-ets.ru":
            return None
        next_cursor = checkpoint.get("next_cursor")
        if not isinstance(next_cursor, str) or not CURSOR.fullmatch(next_cursor):
            return None
        seen = checkpoint.get("seen_ids")
        if not isinstance(seen, list) or not all(isinstance(value, str) for value in seen):
            return None
        expected_page = int(next_cursor.split(":", 1)[0])
        prior_digest = next_cursor.split(":", 1)[1]
        if self._prepared_pages:
            prefix_pages = [
                self._prepared_pages.get(page)
                for page in range(1, expected_page)
            ]
            if any(page is None for page in prefix_pages):
                self._reset_catalogue_state()
                return None
            prefix_ids: set[str] = set()
            overlap_count = 0
            totals: list[int] = []
            for page in prefix_pages:
                assert page is not None
                ids = set(self._ids(page))
                overlap_count += len(prefix_ids.intersection(ids))
                prefix_ids.update(ids)
                totals.append(int(page["total"]))
            anchor = prefix_pages[-1] if prefix_pages else None
            anchor_digest = (
                hashlib.sha256("\n".join(sorted(self._ids(anchor))).encode("ascii")).hexdigest()
                if anchor is not None else None
            )
            if prefix_ids != set(seen) or anchor_digest != prior_digest or not totals:
                self._reset_catalogue_state()
                return None
            self._total = totals[0]
            self._minimum_total = min(totals)
            self._maximum_total = max(totals)
            first = prefix_pages[0]
            assert first is not None
            self._page_size = len(self._ids(first))
            self._overlap_count = overlap_count
        else:
            self._total = int(checkpoint["total"])
            self._minimum_total = int(checkpoint["minimum_total"])
            self._maximum_total = int(checkpoint["maximum_total"])
            self._page_size = int(checkpoint["page_size"])
            self._overlap_count = int(checkpoint.get("overlap_count") or 0)
        self._seen = set(seen)
        self._expected_page = expected_page
        self._prior_digest = prior_digest
        return next_cursor

    def _resume_checkpoint(self, next_cursor: str | None) -> dict | None:
        if next_cursor is None:
            return None
        return {
            "version": 1,
            "source": "m-ets.ru",
            "next_cursor": next_cursor,
            "seen_ids": sorted(self._seen),
            "total": self._total,
            "minimum_total": self._minimum_total,
            "maximum_total": self._maximum_total,
            "page_size": self._page_size,
            "overlap_count": self._overlap_count,
        }

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

        parsed = self._parsed_page(page_number)
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
        reused = [item for item in novel_observations if self._can_reuse_detail(item)]
        reused_ids = [item[0] for item in reused]
        detail_candidates = [item for item in novel_observations if item[0] not in set(reused_ids)]
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
                    html = pilot._get(url, before_request=workers.acquire)
                else:
                    # Unknown/custom transports are not assumed thread-safe.
                    with self._custom_detail_lock:
                        workers.acquire(url)
                        html = self.pilot._get(url)
                lot = parse_mets_lot(html, url)
                if lot.external_id != external_id:
                    raise MetsEvidenceError("M-ETS detail identity mismatch")
                expected_status = self._expected_status(catalogue_status)
                if lot.auction_status != expected_status:
                    raise MetsEvidenceError("M-ETS detail status differs from catalogue")
                detail_prices = {
                    Decimal(str(value))
                    for value in (lot.start_price, lot.current_price)
                    if value is not None
                }
                if Decimal(catalogue_price) not in detail_prices:
                    raise MetsEvidenceError("M-ETS detail price differs from catalogue")
                if lot.category == "land":
                    return None, "standalone_land_out_of_scope"
                if not _property_ready(lot):
                    return None, "property_evidence_missing"
            except requests.RequestException as exc:
                raise MetsCatalogueScanError(
                    "M-ETS detail network failure; no source reconciliation"
                ) from exc
            except MetsEvidenceError as exc:
                message = str(exc).casefold()
                # HTTP refusal or provider errors are systemic, not a bad lot.
                if any(marker in message for marker in (
                    "http ", "transient http", "provider access explicitly refused",
                    "redirect from public",
                )):
                    raise MetsCatalogueScanError(
                        "M-ETS detail access/refusal failure; no source reconciliation"
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
                "detail_verified_at": datetime.now(timezone.utc).isoformat(),
                "catalogue_fingerprint": self._catalogue_fingerprint(item),
            }
            return lot, None

        # map preserves catalogue ordering and propagates any fatal failure.
        # Only details overlap; the next catalogue page is never fetched early.
        with workers.pool() as executor:
            for lot, code in workers.map_ordered(executor, verify, detail_candidates):
                if code:
                    rejected[code] = rejected.get(code, 0) + 1
                elif lot is not None:
                    accepted.append(lot)

        # Do not certify an apparently empty property catalogue when every
        # independent detail failed its evidence checks.
        if len(detail_candidates) >= 5 and not accepted and not reused_ids:
            raise MetsCatalogueScanError(
                "M-ETS systemic detail verification failure: zero verified "
                "property lots in a selected catalogue page"
            )

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
                "verified_property_lots": len(accepted) + len(reused_ids),
                "details_rejected": sum(rejected.values()),
                "detail_attempts": len(detail_candidates),
                "detail_skipped_unchanged": len(reused_ids),
                "reused_existing_ids": reused_ids,
                "reused_catalogue_fingerprints": {
                    item[0]: self._catalogue_fingerprint(item) for item in reused
                },
                "detail_concurrency": filters.detail_concurrency,
                "rejection_codes": rejected,
                "requested_category_group": "mets_property_nonreconciling",
                "complete_selected_filter_inventory": next_cursor is None,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "resume_checkpoint": self._resume_checkpoint(next_cursor),
            },
        )

    async def search(
        self, filters: MetsSyncFilters, cursor: str | None = None,
    ) -> ConnectorPage:
        return await asyncio.to_thread(self._search, filters, cursor)
