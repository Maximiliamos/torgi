from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bankrotai.connectors.base import AuctionConnector
from bankrotai.connectors.registry import connector_registry
from bankrotai.db import LotSyncRun, LotSyncSourceRun, ProcessedLot, SourceLot, utc_now
from bankrotai.logic import persist_lot, reconcile_cross_source_duplicates
from bankrotai.scraper_contracts import LotOnlineSearchFilters, TBankrotSearchFilters, TorgiGovSearchFilters
from bankrotai.scrapers import LotOnlineClient, TBankrotClient, TorgiGovClient, is_sale_real_estate_lot


ACTIVE_STATUSES = {"active", "published", "open", "scheduled", "applications_submission"}


@dataclass(frozen=True, slots=True)
class SourceSyncSpec:
    source_id: str
    filters: Any


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
    duplicates_merged: int = 0
    error: str | None = None
    seen_external_ids: set[str] = field(default_factory=set, repr=False)


def default_source_specs() -> tuple[SourceSyncSpec, ...]:
    return (
        SourceSyncSpec(
            "torgi.gov.ru",
            TorgiGovSearchFilters(
                type_transaction="SALE",
                category_code=TorgiGovClient.REAL_ESTATE_CATEGORY_CODES,
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
    ) -> None:
        self.session_factory = session_factory
        self.connector_factory = connector_factory
        self.lease_minutes = lease_minutes
        self.max_pages_per_source = max_pages_per_source

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
        results: list[SourceSyncResult] = []
        for spec in specs:
            result = await self._sync_source(run_id, spec)
            results.append(result)
        with self.session_factory() as session:
            merged = reconcile_cross_source_duplicates(session)
            session.commit()
        if results:
            results[-1].duplicates_merged += merged
        status = "success" if all(item.status == "success" for item in results) else (
            "failed" if all(item.status == "failed" for item in results) else "partial"
        )
        payload = {"status": status, "sources": [self._result_payload(item) for item in results]}
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
        self._upsert_source_run(run_id, result, started_at=started)
        connector = self.connector_factory(spec.source_id)
        cursor: str | None = None
        try:
            for _page_number in range(1, self.max_pages_per_source + 1):
                page = await connector.search(spec.filters, cursor)
                result.pages_scanned += 1
                self._persist_page(run_id, result, page.items)
                self._heartbeat(run_id)
                if page.next_cursor is None:
                    result.complete_source_run = True
                    break
                cursor = page.next_cursor
            if not result.complete_source_run:
                raise RuntimeError("source pagination exceeded the safety page limit")
            result.items_archived = self._archive_missing_after_complete_run(run_id, spec.source_id)
            result.status = "success"
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
        self._upsert_source_run(run_id, result, started_at=started, finished_at=utc_now())
        return result

    def _persist_page(self, run_id: str, result: SourceSyncResult, lots: list[Any]) -> None:
        with self.session_factory() as session:
            for normalized in lots:
                if not is_sale_real_estate_lot(normalized):
                    continue
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
            session.commit()

    def _archive_missing_after_complete_run(self, run_id: str, source_id: str) -> int:
        archived = 0
        with self.session_factory() as session:
            rows = session.scalars(select(SourceLot).where(
                SourceLot.source_system == source_id,
                SourceLot.is_archived.is_(False),
                or_(SourceLot.last_sync_run_id.is_(None), SourceLot.last_sync_run_id != run_id),
            )).all()
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
    def _result_payload(result: SourceSyncResult) -> dict[str, Any]:
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
            "duplicates_merged": result.duplicates_merged,
            "error": result.error,
        }


def run_nationwide_sync(
    session_factory: Callable[[], Session],
    run_id: str,
    specs: tuple[SourceSyncSpec, ...] | None = None,
) -> dict[str, Any]:
    return asyncio.run(NationwideIngestionService(session_factory).run(run_id, specs or default_source_specs()))
