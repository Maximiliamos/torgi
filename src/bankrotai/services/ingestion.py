from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session

from bankrotai.connectors.base import AuctionConnector
from bankrotai.connectors.registry import connector_registry
from bankrotai.db import LotSyncRun, LotSyncSourceRun, ProcessedLot, SourceLot, utc_now
from bankrotai.logic import (
    _raw_value,
    _to_datetime,
    _to_decimal,
    normalize_region_code,
    normalize_status,
    persist_lot,
    reconcile_cross_source_duplicates,
)
from bankrotai.scraper_contracts import (
    BidExpertSearchFilters,
    LotOnlineSearchFilters,
    TBankrotSearchFilters,
    TorgiGovSearchFilters,
    TorgiRussiaSearchFilters,
)
from bankrotai.scrapers import LotOnlineClient, TBankrotClient, TorgiGovClient, is_sale_real_estate_lot
from bankrotai.services.batch_persistence import persist_changed_lots_batch


logger = logging.getLogger(__name__)


ACTIVE_STATUSES = {"active", "published", "open", "scheduled", "applications_submission"}
MIN_COVERAGE_GUARD_BASELINE = 20
MIN_COMPLETE_RUN_COVERAGE_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class SourceSyncSpec:
    source_id: str
    filters: Any
    archive_region_code: str | None = None
    reconcile_missing: bool = True
    max_batches: int | None = None


@dataclass(slots=True)
class SourceSyncResult:
    source_system: str
    status: str = "running"
    complete_source_run: bool = False
    pages_scanned: int = 0
    items_seen: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    items_archived: int = 0
    items_failed: int = 0
    items_duplicates: int = 0
    duplicates_merged: int = 0
    category_pages: dict[str, int] = field(default_factory=dict)
    timing_ms: dict[str, float] = field(default_factory=dict)
    sql_statements: int = 0
    http_requests: int = 0
    response_bytes: int = 0
    elapsed_seconds: float = 0.0
    current_category: str | None = None
    total_pages: int | None = None
    error: str | None = None
    seen_external_ids: set[str] = field(default_factory=set, repr=False)


def default_source_specs() -> tuple[SourceSyncSpec, ...]:
    return (
        SourceSyncSpec(
            "torgi.gov.ru",
            TorgiGovSearchFilters(
                type_transaction="SALE",
                category_code=TorgiGovClient.REAL_ESTATE_ROOT_CATEGORY_CODES,
                lot_status=TorgiGovClient.DEFAULT_LOT_STATUS,
                page=1,
                page_size=100,
            ),
        ),
        SourceSyncSpec(
            "tbankrot.ru",
            TBankrotSearchFilters(
                category_codes=TBankrotClient.REAL_ESTATE_CATEGORY_CODES,
                page=1,
                page_size=100,
            ),
        ),
        SourceSyncSpec(
            "lot-online.ru",
            LotOnlineSearchFilters(
                category_id=LotOnlineClient.DEFAULT_CATEGORY_ID,
                archive_mode="false",
                page=1,
                page_size=96,
            ),
        ),
        SourceSyncSpec(
            "torgi-russia.ru",
            TorgiRussiaSearchFilters(category_id="6", history_only=False, page=1),
        ),
        SourceSyncSpec(
            "bidexpert.ru",
            BidExpertSearchFilters(category="all", page=1),
        ),
    )


def fast_source_specs(*, gis_publish_date_from: str) -> tuple[SourceSyncSpec, ...]:
    """Bounded discovery for the UI; it never proves coverage or reconciles missing lots."""
    specs = []
    for spec in default_source_specs():
        filters = spec.filters
        if spec.source_id == "torgi.gov.ru":
            filters = replace(filters, publish_date_from=gis_publish_date_from)
        specs.append(replace(spec, filters=filters, reconcile_missing=False, max_batches=1))
    return tuple(specs)


def source_full_specs(source_id: str) -> tuple[SourceSyncSpec, ...]:
    """Return one complete, archive-safe source specification.

    This is used for controlled VPN-OFF source validation.  It deliberately
    retains full pagination and missing reconciliation, unlike fast refresh.
    """
    for spec in default_source_specs():
        if spec.source_id == source_id:
            return (spec,)
    raise ValueError(f"Unsupported source-only full sync: {source_id}")


def regional_source_specs(
    *,
    region_code: str,
    region_name: str,
) -> tuple[SourceSyncSpec, ...]:
    """Build a complete regional pilot without reconciling other regions."""
    return (
        SourceSyncSpec(
            "torgi.gov.ru",
            TorgiGovSearchFilters(
                type_transaction="SALE",
                category_code=TorgiGovClient.REAL_ESTATE_ROOT_CATEGORY_CODES,
                lot_status=TorgiGovClient.DEFAULT_LOT_STATUS,
                subject_rf=region_code,
                page=1,
                page_size=100,
            ),
            archive_region_code=region_code,
        ),
        SourceSyncSpec(
            "tbankrot.ru",
            TBankrotSearchFilters(
                category_codes=TBankrotClient.REAL_ESTATE_CATEGORY_CODES,
                region=TBankrotClient.normalize_region_filter(region_code),
                page=1,
                page_size=100,
            ),
            archive_region_code=region_code,
        ),
        SourceSyncSpec(
            "lot-online.ru",
            LotOnlineSearchFilters(
                category_id=LotOnlineClient.DEFAULT_CATEGORY_ID,
                region_feature=region_name,
                archive_mode="false",
                page=1,
                page_size=96,
            ),
            archive_region_code=region_code,
        ),
    )


class SyncAlreadyRunningError(RuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"Nationwide synchronization is already running: {run_id}")
        self.run_id = run_id


class NationwideIngestionService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        connector_factory: Callable[[str], AuctionConnector] = connector_registry.create,
        lease_minutes: int = 10,
        max_pages_per_source: int = 10_000,
        profile_timings: bool = False,
        use_gis_batch_persistence: bool = True,
        gis_batch_size: int = 500,
    ) -> None:
        self.session_factory = session_factory
        self.connector_factory = connector_factory
        self.lease_minutes = lease_minutes
        self.max_pages_per_source = max_pages_per_source
        self.profile_timings = profile_timings
        self.use_gis_batch_persistence = use_gis_batch_persistence
        self.gis_batch_size = gis_batch_size

    def create_run(self, *, triggered_by: str | None, trigger_type: str, total_sources: int) -> str:
        now = utc_now()
        with self.session_factory() as session:
            active = session.scalar(
                select(LotSyncRun)
                .where(
                    LotSyncRun.status.in_(("queued", "running")),
                    LotSyncRun.lease_expires_at.isnot(None),
                    LotSyncRun.lease_expires_at > now,
                )
                .order_by(LotSyncRun.created_at.desc())
            )
            if active is not None:
                raise SyncAlreadyRunningError(active.id)
            run_id = str(uuid.uuid4())
            session.add(LotSyncRun(
                id=run_id,
                triggered_by=triggered_by,
                trigger_type=trigger_type,
                status="queued",
                total_sources=total_sources,
                heartbeat_at=now,
                lease_owner=socket.gethostname(),
                lease_expires_at=now + timedelta(minutes=self.lease_minutes),
                created_at=now,
            ))
            session.commit()
            return run_id

    async def run(self, run_id: str, specs: tuple[SourceSyncSpec, ...]) -> dict[str, Any]:
        self._mark_run_running(run_id)
        if specs and all(not spec.reconcile_missing for spec in specs):
            results = list(await asyncio.gather(*(self._sync_source(run_id, spec) for spec in specs)))
        else:
            results = []
            for spec in specs:
                result = await self._sync_source(run_id, spec)
                results.append(result)
        dedupe_started = time.perf_counter()
        with self.session_factory() as session:
            merged = reconcile_cross_source_duplicates(session)
            session.commit()
        canonical_dedupe_ms = round((time.perf_counter() - dedupe_started) * 1000, 3)
        if results:
            results[-1].duplicates_merged += merged
        status = "success" if all(item.status == "success" for item in results) else (
            "failed" if all(item.status == "failed" for item in results) else "partial"
        )
        payload = {
            "status": status,
            "sources": [self._result_payload(item) for item in results],
            "profile": {"canonical_dedupe_ms": canonical_dedupe_ms} if self.profile_timings else {},
        }
        with self.session_factory() as session:
            run = session.get(LotSyncRun, run_id)
            if run is not None:
                run.status = status
                run.finished_at = utc_now()
                run.heartbeat_at = utc_now()
                run.lease_expires_at = None
                run.result_json = payload
                session.commit()
        return payload

    async def _sync_source(self, run_id: str, spec: SourceSyncSpec) -> SourceSyncResult:
        result = SourceSyncResult(source_system=spec.source_id)
        started = utc_now()
        wall_started = time.perf_counter()
        active_baseline = self._active_source_count(
            spec.source_id,
            region_code=spec.archive_region_code,
        )
        self._upsert_source_run(run_id, result, started_at=started)
        connector = self.connector_factory(spec.source_id)
        cursor: str | None = None
        try:
            page_limit = min(self.max_pages_per_source, spec.max_batches or self.max_pages_per_source)
            reached_source_end = False
            for _page_number in range(1, page_limit + 1):
                page = await connector.search(spec.filters, cursor)
                pages_fetched = max(1, int(page.metadata.get("pages_fetched") or 1))
                result.pages_scanned += pages_fetched
                result.current_category = page.metadata.get("current_category") or page.metadata.get(
                    "requested_category_group"
                )
                if page.metadata.get("total_pages") is not None:
                    result.total_pages = int(page.metadata["total_pages"])
                if self.profile_timings:
                    for key, value in page.metadata.get("timings", {}).items():
                        if isinstance(value, (int, float)):
                            if key.endswith("_ms"):
                                result.timing_ms[key] = result.timing_ms.get(key, 0.0) + float(value)
                            elif key == "http_requests":
                                result.http_requests += int(value)
                            elif key == "response_bytes":
                                result.response_bytes += int(value)
                category_page_counts = page.metadata.get("category_pages")
                if isinstance(category_page_counts, dict):
                    for group, count in category_page_counts.items():
                        result.category_pages[str(group)] = result.category_pages.get(str(group), 0) + int(count)
                category_group = page.metadata.get("requested_category_group")
                if category_group and not isinstance(category_page_counts, dict):
                    group = str(category_group)
                    result.category_pages[group] = result.category_pages.get(group, 0) + 1
                await self._persist_page(run_id, result, page.items, connector=connector)
                result.elapsed_seconds = time.perf_counter() - wall_started
                self._heartbeat(run_id)
                self._upsert_source_run(run_id, result, started_at=started)
                if page.next_cursor is None:
                    reached_source_end = True
                    break
                cursor = page.next_cursor
            if not spec.reconcile_missing:
                result.complete_source_run = False
                result.status = "success"
                result.elapsed_seconds = time.perf_counter() - wall_started
                self._upsert_source_run(run_id, result, started_at=started, finished_at=utc_now())
                return result
            result.complete_source_run = reached_source_end
            if not reached_source_end:
                raise RuntimeError("source pagination exceeded the safety page limit")
            if (
                active_baseline >= MIN_COVERAGE_GUARD_BASELINE
                and result.items_seen < active_baseline * MIN_COMPLETE_RUN_COVERAGE_RATIO
            ):
                result.complete_source_run = False
                raise RuntimeError(
                    "source coverage guard rejected reconciliation: "
                    f"seen={result.items_seen}, active_baseline={active_baseline}"
                )
            result.items_archived = self._archive_missing_after_complete_run(
                run_id,
                spec.source_id,
                region_code=spec.archive_region_code,
            )
            result.status = "success"
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
        result.elapsed_seconds = time.perf_counter() - wall_started
        self._upsert_source_run(run_id, result, started_at=started, finished_at=utc_now())
        return result

    def _active_source_count(self, source_id: str, *, region_code: str | None) -> int:
        with self.session_factory() as session:
            query = select(SourceLot.id).where(
                SourceLot.source_system == source_id,
                SourceLot.is_archived.is_(False),
            )
            if region_code is not None:
                query = query.where(SourceLot.region_code == region_code)
            return len(session.scalars(query).all())

    async def _persist_page(
        self,
        run_id: str,
        result: SourceSyncResult,
        lots: list[Any],
        *,
        connector: AuctionConnector | None = None,
    ) -> None:
        batch_started = time.perf_counter()
        accepted = []
        page_ids: set[str] = set()
        for lot in lots:
            if not is_sale_real_estate_lot(lot):
                continue
            if lot.external_id in result.seen_external_ids or lot.external_id in page_ids:
                result.items_duplicates += 1
                continue
            page_ids.add(lot.external_id)
            accepted.append(lot)
        if result.source_system == "lot-online.ru" and accepted and connector is not None:
            with self.session_factory() as lookup_session:
                existing_lot_online = {
                    row.external_id: row
                    for row in lookup_session.scalars(select(SourceLot).where(
                        SourceLot.source_system == result.source_system,
                        SourceLot.external_id.in_([lot.external_id for lot in accepted]),
                    )).all()
                }
            enrichment_limit = asyncio.Semaphore(4)

            async def enrich_if_needed(lot: Any) -> None:
                existing = existing_lot_online.get(lot.external_id)
                previous_raw = existing.raw_data if existing and isinstance(existing.raw_data, dict) else {}
                current_raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
                needs_detail = (
                    existing is None
                    or previous_raw.get("listing_fingerprint") != current_raw.get("listing_fingerprint")
                    or previous_raw.get("detail_enrichment_status") != "success"
                )
                if needs_detail:
                    try:
                        async with enrichment_limit:
                            await connector.enrich_lot(lot)
                    except Exception as exc:
                        logger.warning("LOT-ONLINE detail enrichment failed for %s: %s", lot.external_id, exc)
                        current_raw["detail_enrichment_status"] = "failed"
                        lot.raw_data = current_raw
                    return
                assert existing is not None
                lot.address = existing.address or lot.address
                lot.cadastral_number = existing.cadastral_number or lot.cadastral_number
                lot.description = existing.description or lot.description
                lot.detail_level = "detail"
                lot.raw_data = {**current_raw, **previous_raw}

            await asyncio.gather(*(enrich_if_needed(lot) for lot in accepted))
        with self.session_factory() as session:
            bind = session.get_bind()

            def count_statement(*_args: Any, **_kwargs: Any) -> None:
                result.sql_statements += 1

            if self.profile_timings:
                event.listen(bind, "before_cursor_execute", count_statement)
            if result.source_system != "torgi.gov.ru" or not self.use_gis_batch_persistence:
                self._persist_page_legacy(session, run_id, result, accepted)
                commit_started = time.perf_counter()
                session.commit()
                self._add_timing(result, "commit_ms", commit_started)
                if self.profile_timings:
                    event.remove(bind, "before_cursor_execute", count_statement)
                self._add_timing(result, "total_batch_ms", batch_started)
                return
            external_ids = [lot.external_id for lot in accepted]
            lookup_started = time.perf_counter()
            existing_rows = {
                row.external_id: row
                for row in session.scalars(select(SourceLot).where(
                    SourceLot.source_system == result.source_system,
                    SourceLot.external_id.in_(external_ids),
                )).all()
            } if external_ids else {}
            self._add_timing(result, "db_lookup_ms", lookup_started)
            changed_or_new: list[Any] = []
            for normalized in accepted:
                result.items_seen += 1
                result.seen_external_ids.add(normalized.external_id)
                source_row = existing_rows.get(normalized.external_id)
                existed = source_row is not None
                before = self._source_fingerprint(source_row) if source_row is not None else None
                if (
                    source_row is not None
                    and source_row.processed_lot_id is not None
                    and before == self._normalized_source_fingerprint(normalized, source_row)
                ):
                    source_row.last_sync_run_id = run_id
                    source_row.last_seen_at = utc_now()
                    source_row.missing_successful_runs = 0
                    result.items_unchanged += 1
                    continue
                changed_or_new.append(normalized)
                if existed:
                    result.items_updated += 1
                else:
                    result.items_inserted += 1
            persist_started = time.perf_counter()
            persist_changed_lots_batch(
                session,
                changed_or_new,
                run_id,
                batch_size=self.gis_batch_size,
                existing_sources=existing_rows,
            )
            self._add_timing(result, "persist_ms", persist_started)
            commit_started = time.perf_counter()
            session.commit()
            self._add_timing(result, "commit_ms", commit_started)
            if self.profile_timings:
                event.remove(bind, "before_cursor_execute", count_statement)
        self._add_timing(result, "total_batch_ms", batch_started)

    def _persist_page_legacy(
        self,
        session: Session,
        run_id: str,
        result: SourceSyncResult,
        lots: list[Any],
    ) -> None:
        for normalized in lots:
            result.items_seen += 1
            result.seen_external_ids.add(normalized.external_id)
            source_row = session.scalar(select(SourceLot).where(
                SourceLot.source_system == normalized.source_system,
                SourceLot.external_id == normalized.external_id,
            ))
            existed = source_row is not None
            before = self._source_fingerprint(source_row) if source_row is not None else None
            persist_lot(session, normalized)
            source_row = session.scalar(select(SourceLot).where(
                SourceLot.source_system == normalized.source_system,
                SourceLot.external_id == normalized.external_id,
            ))
            if source_row is None:
                result.items_failed += 1
                continue
            source_row.last_sync_run_id = run_id
            source_row.missing_successful_runs = 0
            if existed:
                changed = self._source_fingerprint(source_row) != before
                result.items_updated += int(changed)
                result.items_unchanged += int(not changed)
            else:
                result.items_inserted += 1

    def _add_timing(self, result: SourceSyncResult, key: str, started: float) -> None:
        if not self.profile_timings:
            return
        result.timing_ms[key] = result.timing_ms.get(key, 0.0) + (time.perf_counter() - started) * 1000

    def _archive_missing_after_complete_run(
        self,
        run_id: str,
        source_id: str,
        *,
        region_code: str | None = None,
    ) -> int:
        archived = 0
        with self.session_factory() as session:
            query = select(SourceLot).where(
                SourceLot.source_system == source_id,
                SourceLot.is_archived.is_(False),
                or_(SourceLot.last_sync_run_id.is_(None), SourceLot.last_sync_run_id != run_id),
            )
            if region_code is not None:
                query = query.where(SourceLot.region_code == region_code)
            rows = session.scalars(query).all()
            for row in rows:
                row.missing_successful_runs += 1
                if row.missing_successful_runs < 2:
                    continue
                row.is_active = False
                row.is_archived = True
                row.archived_at = utc_now()
                row.archive_reason = "missing_after_two_complete_syncs"
                if row.processed_lot_id:
                    processed = session.get(ProcessedLot, row.processed_lot_id)
                    if processed is not None:
                        processed.is_archived = True
                        processed.archived_at = processed.archived_at or utc_now()
                archived += 1
            session.commit()
        return archived

    def _mark_run_running(self, run_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(LotSyncRun, run_id)
            if run is None:
                raise KeyError(run_id)
            run.status = "running"
            run.started_at = run.started_at or utc_now()
            session.commit()

    def _heartbeat(self, run_id: str) -> None:
        now = utc_now()
        with self.session_factory() as session:
            run = session.get(LotSyncRun, run_id)
            if run is not None:
                run.heartbeat_at = now
                run.lease_expires_at = now + timedelta(minutes=self.lease_minutes)
                session.commit()

    def _upsert_source_run(
        self,
        run_id: str,
        result: SourceSyncResult,
        *,
        started_at: Any = None,
        finished_at: Any = None,
    ) -> None:
        with self.session_factory() as session:
            row = session.scalar(select(LotSyncSourceRun).where(
                LotSyncSourceRun.sync_run_id == run_id,
                LotSyncSourceRun.source_system == result.source_system,
            ))
            if row is None:
                row = LotSyncSourceRun(sync_run_id=run_id, source_system=result.source_system)
                session.add(row)
            row.status = result.status
            row.complete_source_run = result.complete_source_run
            row.pages_scanned = result.pages_scanned
            row.items_seen = result.items_seen
            row.items_inserted = result.items_inserted
            row.items_updated = result.items_updated
            row.items_unchanged = result.items_unchanged
            row.items_archived = result.items_archived
            row.items_failed = result.items_failed
            row.duplicates_merged = result.duplicates_merged
            row.error_message = result.error
            row.checkpoint_json = {
                "category_pages": result.category_pages,
                "current_category": result.current_category,
                "total_pages": result.total_pages,
                "rows_per_second": round(result.items_seen / result.elapsed_seconds, 3)
                if result.elapsed_seconds else 0,
            } if result.category_pages or result.current_category else None
            row.started_at = row.started_at or started_at
            row.finished_at = finished_at
            session.commit()

    @staticmethod
    def _source_fingerprint(row: SourceLot) -> tuple[Any, ...]:
        return (
            row.title,
            row.description,
            row.category,
            row.region_code,
            row.address,
            row.cadastral_number,
            row.start_price,
            row.current_price,
            row.source_status,
            row.application_deadline,
            row.auction_at,
            row.source_updated_at,
            row.is_active,
            row.is_archived,
        )

    @staticmethod
    def _normalized_source_fingerprint(normalized: Any, existing: SourceLot) -> tuple[Any, ...]:
        raw = normalized.raw_data or {}
        normalized_status = normalize_status(normalized.auction_status)
        is_active = existing.is_active
        is_archived = existing.is_archived
        if normalized_status == "closed":
            is_active, is_archived = False, True
        elif normalized_status in {"active", "scheduled"}:
            is_active, is_archived = True, False
        return (
            normalized.title,
            normalized.description,
            normalized.category,
            normalize_region_code(str(raw.get("region_code") or normalized.region_name or normalized.region_slug or "")),
            normalized.address,
            normalized.cadastral_number,
            _to_decimal(normalized.start_price),
            _to_decimal(normalized.current_price),
            normalized.auction_status,
            normalized.application_deadline or _to_datetime(_raw_value(raw, "bidd_end_time", "application_deadline")),
            normalized.auction_at or _to_datetime(_raw_value(raw, "auction_start_date", "auction_at")),
            _to_datetime(_raw_value(raw, "updated_at", "source_updated_at", "last_update")),
            is_active,
            is_archived,
        )

    def _result_payload(self, result: SourceSyncResult) -> dict[str, Any]:
        return {
            "source_system": result.source_system,
            "status": result.status,
            "complete_source_run": result.complete_source_run,
            "pages_scanned": result.pages_scanned,
            "items_seen": result.items_seen,
            "items_inserted": result.items_inserted,
            "items_updated": result.items_updated,
            "items_unchanged": result.items_unchanged,
            "items_archived": result.items_archived,
            "items_failed": result.items_failed,
            "items_duplicates": result.items_duplicates,
            "duplicates_merged": result.duplicates_merged,
            "category_pages": result.category_pages,
            "current_category": result.current_category,
            "total_pages": result.total_pages,
            "profile": {
                **{key: round(value, 3) for key, value in result.timing_ms.items()},
                "sql_statements": result.sql_statements,
                "http_requests": result.http_requests,
                "response_bytes": result.response_bytes,
                "sql_statements_per_100_lots": round(result.sql_statements * 100 / result.items_seen, 2)
                if result.items_seen else 0,
                "rows_per_second": round(result.items_seen / result.elapsed_seconds, 3)
                if result.elapsed_seconds else 0,
                "pages_per_second": round(result.pages_scanned / result.elapsed_seconds, 3)
                if result.elapsed_seconds else 0,
            } if self.profile_timings else {},
            "error": result.error,
        }


def run_nationwide_sync(
    session_factory: Callable[[], Session],
    run_id: str,
    specs: tuple[SourceSyncSpec, ...] | None = None,
) -> dict[str, Any]:
    return asyncio.run(NationwideIngestionService(session_factory).run(run_id, specs or default_source_specs()))
