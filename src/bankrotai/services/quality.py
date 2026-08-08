from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import (
    DiagnosticEvent,
    GeoFailure,
    LotDocumentVersion,
    LotGeoSnapshot,
    ProcessedLot,
    RawLot,
    SourceHealthState,
    SourceLot,
)
from bankrotai.dto import DataQualityDTO, SourceHealthDTO


def data_quality_snapshot(session: Session) -> DataQualityDTO:
    scalar = session.scalar
    return DataQualityDTO(
        total_lots=scalar(select(func.count()).select_from(ProcessedLot)) or 0,
        active_lots=scalar(select(func.count()).where(ProcessedLot.auction_status == "active")) or 0,
        archived_lots=scalar(select(func.count()).where(ProcessedLot.is_archived.is_(True))) or 0,
        duplicate_lots=scalar(select(func.count()).where(ProcessedLot.duplicate_of_id.isnot(None))) or 0,
        missing_address=scalar(
            select(func.count()).where((ProcessedLot.address.is_(None)) | (func.trim(ProcessedLot.address) == ""))
        )
        or 0,
        missing_cadastre=scalar(
            select(func.count()).where(
                (ProcessedLot.cadastral_number.is_(None)) | (func.trim(ProcessedLot.cadastral_number) == "")
            )
        )
        or 0,
        missing_price=scalar(
            select(func.count()).where(ProcessedLot.current_price.is_(None), ProcessedLot.start_price.is_(None))
        )
        or 0,
        geocoded_lots=scalar(select(func.count(func.distinct(LotGeoSnapshot.lot_id)))) or 0,
        geo_attention_lots=scalar(select(func.count()).where(ProcessedLot.needs_geo_check.is_(True))) or 0,
        queued_geo_failures=scalar(
            select(func.count()).where(GeoFailure.status.not_in(("resolved", "terminal")))
        )
        or 0,
        unknown_status_lots=scalar(select(func.count()).where(ProcessedLot.auction_status == "unknown")) or 0,
        ai_analyzed_lots=scalar(
            select(func.count()).where(
                (ProcessedLot.market_price.isnot(None)) | (ProcessedLot.ai_recommendation.isnot(None))
            )
        )
        or 0,
        document_versions=scalar(select(func.count()).select_from(LotDocumentVersion)) or 0,
    )


def list_source_health(session: Session) -> list[SourceHealthDTO]:
    states = {row.source_system: row for row in session.scalars(select(SourceHealthState)).all()}
    counts = dict(
        session.execute(select(ProcessedLot.source_system, func.count()).group_by(ProcessedLot.source_system)).all()
    )
    names = sorted(set(states) | set(counts))
    return [
        SourceHealthDTO(
            source_system=name,
            status=states[name].status if name in states else "not_checked",
            items_seen=states[name].items_seen if name in states else int(counts.get(name, 0)),
            last_success_at=states[name].last_success_at if name in states else None,
            last_failure_at=states[name].last_failure_at if name in states else None,
            last_error=states[name].last_error if name in states else None,
        )
        for name in names
    ]


def update_source_health(
    session: Session,
    source_system: str,
    *,
    status: str,
    items_seen: int | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceHealthState:
    state = session.scalar(select(SourceHealthState).where(SourceHealthState.source_system == source_system))
    if state is None:
        state = SourceHealthState(source_system=source_system)
        session.add(state)
    now = utc_now()
    state.status = status
    state.updated_at = now
    state.metadata_json = metadata
    if status in {"running", "queued"}:
        state.last_started_at = now
    elif status == "healthy":
        state.last_success_at = now
        state.last_error = None
        if items_seen is not None:
            state.items_seen = max(0, items_seen)
    elif status == "failed":
        state.last_failure_at = now
        state.last_error = (error or "Unknown source error")[:5000]
    session.flush()
    return state


def record_geo_failure(
    session: Session,
    lot_id: int,
    error: str,
    *,
    retry_after_seconds: int = 300,
) -> GeoFailure:
    failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
    now = utc_now()
    if failure is None:
        failure = GeoFailure(lot_id=lot_id, error_message=error)
        session.add(failure)
    else:
        failure.attempt_count += 1
        failure.error_message = error
    failure.status = "queued"
    failure.last_failed_at = now
    failure.next_retry_at = now + timedelta(seconds=max(0, retry_after_seconds))
    failure.resolved_at = None
    lot = session.get(ProcessedLot, lot_id)
    if lot is not None:
        lot.needs_geo_check = True
    session.flush()
    return failure


def resolve_geo_failure(session: Session, lot_id: int) -> bool:
    failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
    if failure is None:
        return False
    failure.status = "resolved"
    failure.resolved_at = utc_now()
    failure.next_retry_at = None
    lot = session.get(ProcessedLot, lot_id)
    if lot is not None:
        lot.needs_geo_check = False
    session.flush()
    return True


def geo_retry_lot_ids(session: Session, *, limit: int = 100) -> list[int]:
    now = utc_now()
    return list(
        session.scalars(
            select(GeoFailure.lot_id)
            .where(GeoFailure.status.not_in(("resolved", "terminal")))
            .where((GeoFailure.next_retry_at.is_(None)) | (GeoFailure.next_retry_at <= now))
            .order_by(GeoFailure.last_failed_at)
            .limit(max(1, min(limit, 1000)))
        ).all()
    )


def apply_raw_payload_retention(session: Session, *, retention_days: int = 30) -> dict[str, int]:
    cutoff = utc_now() - timedelta(days=max(1, retention_days))
    raw_deleted = session.execute(delete(RawLot).where(RawLot.created_at < cutoff)).rowcount or 0
    source_cleared = (
        session.execute(
            update(SourceLot).where(SourceLot.created_at < cutoff, SourceLot.raw_data.isnot(None)).values(raw_data=None)
        ).rowcount
        or 0
    )
    session.add(
        DiagnosticEvent(
            severity="info",
            component="retention",
            message="Raw payload retention completed",
            context_json={
                "retention_days": retention_days,
                "raw_deleted": raw_deleted,
                "source_cleared": source_cleared,
            },
        )
    )
    return {"raw_deleted": raw_deleted, "source_cleared": source_cleared}


def record_diagnostic(
    session: Session,
    *,
    severity: str,
    component: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> DiagnosticEvent:
    event = DiagnosticEvent(
        severity=severity,
        component=component,
        message=message,
        context_json=context,
    )
    session.add(event)
    session.flush()
    return event
