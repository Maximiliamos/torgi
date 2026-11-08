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
from datetime import datetime, timezone
from bankrotai.connectors.registry.detail_transport import DetailWorkers, HostRateLimiter
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
from bankrotai.services.incremental_detail_cache import (
    catalogue_fingerprint, snapshot_reusable,
)

CURSOR = re.compile(r"^([1-9]\d{0,4}):([0-9a-f]{64})$")
MAX_REJECTED_DETAIL_RATIO = 0.90
SYSTEMIC_DETAIL_REJECTION_CODES = frozenset({
    "lot_identity_mismatch",
    "missing_lot_identity",
    "price_mismatch",
    "ssr_schema_changed",
    "other_detail_error",
})


def _verified_detail_with_retry(trade_id: str, lot_id: str, session: requests.Session, limiter=None):
    """Retry only transient transport/5xx errors, never identity or 403/429."""
    for attempt in range(3):
        try:
            if limiter is not None:
                limiter.acquire(f"https://torgi.cdtrf.ru/trades/{trade_id}")
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
    detail_concurrency: int = 2
    detail_requests_per_second: float | None = None

    def __post_init__(self):
        if type(self.page_size) is not int or self.page_size not in CDTRF_PAGE_SIZES:
            raise ValueError("CDTRF page_size must be one of 20/50/100")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= MAX_SCAN_PAGES:
            raise ValueError("Unsafe CDTRF max_pages")
        if not 0.5 <= self.detail_delay_seconds <= 10:
            raise ValueError("CDTRF detail_delay_seconds must be 0.5..10")
        if type(self.detail_concurrency) is not int or not 1 <= self.detail_concurrency <= 4:
            raise ValueError("Unsafe CDTRF detail_concurrency")
        if self.detail_requests_per_second is not None and not 0 < self.detail_requests_per_second <= 2:
            raise ValueError("CDTRF detail_requests_per_second must be 0..2")


class CdtrfSourceCrawler:
    """Stateful only within one ingestion run; fail closed on changed pages."""

    def __init__(self, session: requests.Session):
        self.session = session
        self._seen: set[str] = set()
        self._total: int | None = None
        self._pages_total = 0
        self._total_changes = 0
        self._expected_page = 1
        self._prior_digest: str | None = None
        self._detail_limiter: HostRateLimiter | None = None
        self._detail_rate: float | None = None
        self._detail_cache: dict[str, dict] = {}
        self._prepared_pages: dict[int, dict] = {}

    def set_detail_cache(self, snapshots: dict[str, dict]) -> None:
        self._detail_cache = dict(snapshots)

    @staticmethod
    def _catalogue_status_hint(value: object) -> str | None:
        text = " ".join(str(value or "").casefold().split())
        if "прием заяв" in text or "приём заяв" in text:
            return "active"
        if "объяв" in text or "опублик" in text:
            return "scheduled"
        return None

    @staticmethod
    def _catalogue_fingerprint(hit: dict) -> str:
        return catalogue_fingerprint({
            "external_id": hit.get("external_id"),
            "trade_id": hit.get("trade_id"),
            "price": str(hit.get("listing_price_rub")),
            "status": hit.get("source_status_text"),
            "request_time_begin": hit.get("request_time_begin"),
            "request_time_end": hit.get("request_time_end"),
            "region_id": hit.get("region_id"),
            "categories": hit.get("property_category_ids"),
        })

    def _can_reuse_detail(self, hit: dict) -> bool:
        status = self._catalogue_status_hint(hit.get("source_status_text"))
        if status is None:
            return False
        return snapshot_reusable(
            self._detail_cache.get(str(hit["external_id"])),
            expected_price=hit.get("listing_price_rub"),
            expected_status=status,
            fingerprint=self._catalogue_fingerprint(hit),
        )

    def _reset_catalogue_state(self) -> None:
        self._seen = set()
        self._total = None
        self._pages_total = 0
        self._total_changes = 0
        self._expected_page = 1
        self._prior_digest = None

    def _fetch_catalogue_page(self, page_no: int, page_size: int) -> tuple[dict, int]:
        response = None
        retry_delays = (0.5, 1.0, 2.0)
        retries = 0
        for attempt in range(len(retry_delays) + 1):
            try:
                response = self.session.get(
                    _url(page_no, page_size),
                    timeout=(5, 20),
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "TORGI-CDTRF-operator-sync/1",
                    },
                )
            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ):
                if attempt == len(retry_delays):
                    raise
                retries += 1
                time.sleep(retry_delays[attempt])
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 504:
                if attempt == len(retry_delays):
                    raise CdtrfCatalogueScanError(
                        f"CDTRF catalogue HTTP {response.status_code}"
                    )
                retries += 1
                time.sleep(retry_delays[attempt])
                continue
            break
        if response is None:
            raise CdtrfCatalogueScanError("CDTRF catalogue retry loop exhausted")
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
            raise CdtrfCatalogueScanError(
                f"CDTRF catalogue HTTP {response.status_code} or oversized"
            )
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise CdtrfCatalogueScanError("CDTRF catalogue response is not JSON")
        return (
            parse_cdtrf_api_page(
                response.json(), page_num=page_no, page_size=page_size,
            ),
            retries,
        )

    def prepare_catalogue(self, filters: CdtrfSyncFilters) -> dict:
        """Materialize the live API catalogue before starting detail validation."""
        if self._seen or self._expected_page != 1:
            raise CdtrfCatalogueScanError("CDTRF catalogue preparation requires a fresh crawler")
        prepared: dict[int, dict] = {}
        page_no = 1
        pages_total = 1
        seen: set[str] = set()
        total_changes = 0
        previous_total: int | None = None
        retries = 0
        while page_no <= pages_total:
            if page_no > filters.max_pages:
                raise CdtrfCatalogueScanError("CDTRF catalogue exceeds page budget")
            data, page_retries = self._fetch_catalogue_page(page_no, filters.page_size)
            retries += page_retries
            total = int(data["reported_total"])
            if previous_total is not None and total != previous_total:
                total_changes += 1
            previous_total = total
            pages_total = max(pages_total, ceil(total / filters.page_size) or 1)
            expected = min(
                filters.page_size,
                max(0, total - (page_no - 1) * filters.page_size),
            )
            if data["observed_rows"] != expected:
                raise CdtrfCatalogueScanError(
                    "CDTRF incomplete page / unsupported server PageSize"
                )
            seen.update(str(value) for value in data["observed_lot_ids"])
            prepared[page_no] = {"data": data, "retry_count": page_retries}
            page_no += 1
        self._prepared_pages = prepared
        self._reset_catalogue_state()
        return {
            "pages": len(prepared),
            "observed_unique_ids": len(seen),
            "reported_total": previous_total,
            "catalogue_total_changes": total_changes,
            "retry_count": retries,
            "phase": "catalogue_discovery_complete",
        }

    def _catalogue_page(self, page_no: int, page_size: int) -> tuple[dict, int]:
        prepared = self._prepared_pages.get(page_no)
        if prepared is not None:
            return prepared["data"], 0
        return self._fetch_catalogue_page(page_no, page_size)

    def restore_checkpoint(self, checkpoint: dict) -> str | None:
        if checkpoint.get("version") != 1 or checkpoint.get("source") != "cdtrf.ru":
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
            raw_prefix_pages = [
                self._prepared_pages.get(page, {}).get("data")
                for page in range(1, expected_page)
            ]
            if any(not isinstance(page, dict) for page in raw_prefix_pages):
                self._reset_catalogue_state()
                return None
            prefix_pages = [
                page for page in raw_prefix_pages if isinstance(page, dict)
            ]
            prefix_ids = {
                str(value)
                for page in prefix_pages
                for value in page["observed_lot_ids"]
            }
            anchor = prefix_pages[-1] if prefix_pages else None
            anchor_ids = set(anchor["observed_lot_ids"]) if anchor is not None else set()
            anchor_digest = hashlib.sha256(
                "\n".join(sorted(anchor_ids)).encode("ascii")
            ).hexdigest() if anchor is not None else None
            if prefix_ids != set(seen) or anchor_digest != prior_digest:
                self._reset_catalogue_state()
                return None
            prepared_totals = [
                int(self._prepared_pages[page]["data"]["reported_total"])
                for page in range(1, expected_page)
            ]
            self._total = prepared_totals[-1]
            self._pages_total = len(self._prepared_pages)
            self._total_changes = sum(
                left != right
                for left, right in zip(prepared_totals, prepared_totals[1:])
            )
        else:
            self._total = int(checkpoint["total"])
            self._pages_total = int(checkpoint["pages_total"])
            self._total_changes = int(checkpoint.get("total_changes") or 0)
        self._seen = set(seen)
        self._expected_page = expected_page
        self._prior_digest = prior_digest
        return next_cursor

    def _resume_checkpoint(self, next_cursor: str | None) -> dict | None:
        if next_cursor is None:
            return None
        return {
            "version": 1,
            "source": "cdtrf.ru",
            "next_cursor": next_cursor,
            "seen_ids": sorted(self._seen),
            "total": self._total,
            "pages_total": self._pages_total,
            "total_changes": self._total_changes,
        }

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
        data, catalogue_retries = self._catalogue_page(page_no, filters.page_size)
        total: int = int(data["reported_total"])
        if self._total is None:
            self._total = total
            if ceil(total / filters.page_size) > filters.max_pages:
                raise CdtrfCatalogueScanError("CDTRF catalogue exceeds page budget")
        elif total != self._total:
            # CDTRF is a live catalogue, not a point-in-time snapshot. New or
            # closed trades routinely change totalCount while verified detail
            # pages are being read. This source is deliberately non-reconciling,
            # so retain the widest observed page boundary and de-duplicate any
            # rows shifted across page boundaries instead of discarding a safe
            # partial import.
            self._total_changes += 1
            self._total = total

        observed_pages_total = ceil(total / filters.page_size) or 1
        self._pages_total = max(self._pages_total, observed_pages_total)
        if self._pages_total > filters.max_pages:
            raise CdtrfCatalogueScanError("CDTRF catalogue exceeds page budget")

        expected = min(filters.page_size, max(0, total - (page_no - 1) * filters.page_size))
        if data["observed_rows"] != expected:
            raise CdtrfCatalogueScanError("CDTRF incomplete page / unsupported server PageSize")
        ids = set(data["observed_lot_ids"])
        overlapping_ids = ids & self._seen
        new_ids = ids - self._seen
        self._seen.update(ids)

        accepted = []
        rejected: dict[str, int] = {}
        candidates = [
            hit for hit in data["property_candidates"]
            if str(hit["trade_lot_id"]) in new_ids
        ]
        reused = [hit for hit in candidates if self._can_reuse_detail(hit)]
        reused_ids = [str(hit["external_id"]) for hit in reused]
        detail_candidates = [
            hit for hit in candidates if str(hit["external_id"]) not in set(reused_ids)
        ]
        rate = filters.detail_requests_per_second or 1.0 / filters.detail_delay_seconds
        if self._detail_limiter is None:
            self._detail_limiter = HostRateLimiter(rate)
            self._detail_rate = rate
        elif self._detail_rate != rate:
            raise CdtrfCatalogueScanError("CDTRF detail rate changed within one crawl")
        workers = DetailWorkers(
            self.session, filters.detail_concurrency, rate,
            limiter=self._detail_limiter,
        )

        def verify(hit):
            try:
                lot = _verified_detail_with_retry(
                    str(hit["trade_id"]), str(hit["trade_lot_id"]),
                    workers.session(), workers,
                )
            except (CdtrfCanaryError, CdtrfEvidenceError) as exc:
                code = _cdtrf_rejection_code(exc)
                if code == "http_or_network_error":
                    raise CdtrfCatalogueScanError(
                        "CDTRF detail HTTP failure; no source reconciliation"
                    ) from exc
                return None, code
            except requests.RequestException as exc:
                raise CdtrfCatalogueScanError(
                    "CDTRF detail network failure; no source reconciliation"
                ) from exc
            except ValueError as exc:
                raise CdtrfCatalogueScanError(
                    "CDTRF unexpected detail validation failure"
                ) from exc
            if not (_property_ready(lot) or (
                lot.auction_status == "closed"
                and lot.cadastral_number
                and lot.lot_url
                and lot.start_price is not None
            )):
                return None, "property_not_eligible"
            if lot.external_id != hit["external_id"]:
                raise CdtrfCatalogueScanError("CDTRF API/detail lot identity mismatch")
            lot.raw_data = {
                **lot.raw_data,
                "source_scope": "full_selected_catalogue_nonreconciling",
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "detail_verified_at": datetime.now(timezone.utc).isoformat(),
                "catalogue_fingerprint": self._catalogue_fingerprint(hit),
            }
            return lot, None

        # Details only are concurrent; catalogue cursor/coverage remain serial.
        with workers.pool() as executor:
            for lot, code in workers.map_ordered(executor, verify, detail_candidates):
                if code:
                    rejected[code] = rejected.get(code, 0) + 1
                elif lot is not None:
                    accepted.append(lot)
        inspected = len(detail_candidates)
        # Content-level exclusions are normal within CDTRF's broad real-estate
        # categories (for example a movable asset mixed into a trade, or a
        # card without enough property evidence).  Only structural detail
        # failures indicate that the provider contract may have changed.
        systemic_rejections = sum(
            count for code, count in rejected.items()
            if code in SYSTEMIC_DETAIL_REJECTION_CODES
        )
        if inspected >= 10 and systemic_rejections / inspected > MAX_REJECTED_DETAIL_RATIO:
            raise CdtrfCatalogueScanError(
                "CDTRF detail acceptance collapsed; source may have changed; "
                + ",".join(f"{key}={value}" for key, value in sorted(rejected.items()))
            )

        pages_total = self._pages_total
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
                "property_hint_candidates": len(candidates),
                "verified_property_lots": len(accepted) + len(reused_ids),
                "details_rejected": sum(rejected.values()),
                "detail_attempts": len(detail_candidates),
                "detail_skipped_unchanged": len(reused_ids),
                "reused_existing_ids": reused_ids,
                "reused_catalogue_fingerprints": {
                    str(hit["external_id"]): self._catalogue_fingerprint(hit) for hit in reused
                },
                "retry_count": catalogue_retries,
                "detail_concurrency": filters.detail_concurrency,
                "systemic_detail_rejections": systemic_rejections,
                "catalogue_total_changes": self._total_changes,
                "catalogue_overlap_rows_skipped": len(overlapping_ids),
                "rejection_codes": rejected,
                "requested_category_group": "cdtrf_property_nonreconciling",
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "resume_checkpoint": self._resume_checkpoint(next_cursor),
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
        require_source_end=True,
    )
