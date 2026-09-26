from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import (
    DiagnosticEvent,
    GeoFailure,
    LotDocumentVersion,
    LotGeoSnapshot,
    LotPriceEvent,
    LotSyncRun,
    LotSyncSourceRun,
    MapDataset,
    MapTile,
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


def operational_quality_report(session: Session, *, stale_days: int = 7, problem_limit: int = 100) -> dict[str, Any]:
    """Repeatable source/data/map health report with actionable lot identifiers."""
    from bankrotai.services.map_view import extract_map_image_urls

    now = utc_now()
    cutoff = now.replace(tzinfo=None) - timedelta(days=max(1, stale_days))
    source_rows = session.execute(
        select(
            SourceLot.source_system,
            SourceLot.is_active,
            SourceLot.is_archived,
            SourceLot.source_status,
            SourceLot.last_seen_at,
            SourceLot.raw_data,
        )
    ).yield_per(1000)
    sources: dict[str, dict[str, Any]] = {}
    for source_system, is_active, is_archived, status, last_seen_at, raw_data in source_rows:
        value = sources.setdefault(source_system, {
            "total": 0, "active": 0, "archived": 0, "with_photos": 0,
            "unknown_status": 0, "stale_active": 0, "last_seen_at": None,
        })
        value["total"] += 1
        value["active"] += int(bool(is_active and not is_archived))
        value["archived"] += int(bool(is_archived))
        value["with_photos"] += int(bool(extract_map_image_urls(raw_data)))
        value["unknown_status"] += int(not status or status == "unknown")
        value["stale_active"] += int(bool(is_active and not is_archived and last_seen_at < cutoff))
        if last_seen_at and (value["last_seen_at"] is None or last_seen_at > value["last_seen_at"]):
            value["last_seen_at"] = last_seen_at

    problem_query = (
        select(SourceLot.id, SourceLot.source_system, SourceLot.external_id, SourceLot.last_seen_at)
        .where(
            SourceLot.is_active.is_(True),
            SourceLot.is_archived.is_(False),
            SourceLot.last_seen_at < cutoff,
        )
        .order_by(SourceLot.last_seen_at, SourceLot.id)
        .limit(max(1, min(problem_limit, 1000)))
    )
    stale_lots = [
        {"source_lot_id": row.id, "source_system": row.source_system, "external_id": row.external_id,
         "last_seen_at": row.last_seen_at}
        for row in session.execute(problem_query)
    ]
    current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
    last_run = session.scalar(select(LotSyncRun).order_by(LotSyncRun.started_at.desc()).limit(1))
    source_without_processed = session.scalar(
        select(func.count()).select_from(SourceLot).where(SourceLot.processed_lot_id.is_(None))
    ) or 0
    recoverable_source_links = session.scalar(
        select(func.count()).select_from(SourceLot).join(
            ProcessedLot,
            (ProcessedLot.source_system == SourceLot.source_system)
            & (ProcessedLot.external_id == SourceLot.external_id),
        ).where(SourceLot.processed_lot_id.is_(None))
    ) or 0
    processed_without_source = session.scalar(
        select(func.count()).select_from(ProcessedLot).where(
            ~select(SourceLot.id).where(SourceLot.processed_lot_id == ProcessedLot.id).exists()
        )
    ) or 0
    current_dataset_count = session.scalar(
        select(func.count()).select_from(MapDataset).where(MapDataset.is_current.is_(True))
    ) or 0
    tile_count_mismatches = session.scalar(
        select(func.count()).select_from(MapDataset).where(
            MapDataset.tile_count
            != select(func.count()).select_from(MapTile).where(MapTile.dataset_id == MapDataset.id).scalar_subquery()
        )
    ) or 0
    return {
        "generated_at": now,
        "stale_after_days": max(1, stale_days),
        "sources": sources,
        "geocoding": {
            "with_coordinates": session.scalar(select(func.count(func.distinct(LotGeoSnapshot.lot_id)))) or 0,
            "needs_attention": session.scalar(
                select(func.count()).where(ProcessedLot.needs_geo_check.is_(True))
            ) or 0,
            "queued_failures": session.scalar(
                select(func.count()).where(GeoFailure.status.not_in(("resolved", "terminal")))
            ) or 0,
        },
        "price_changes_24h": session.scalar(
            select(func.count()).where(LotPriceEvent.observed_at >= now.replace(tzinfo=None) - timedelta(hours=24))
        ) or 0,
        "unknown_status_lots": session.scalar(
            select(func.count()).where(ProcessedLot.auction_status == "unknown")
        ) or 0,
        "integrity": {
            "source_without_processed": source_without_processed,
            "recoverable_source_links": recoverable_source_links,
            "processed_without_source": processed_without_source,
            "active_and_archived_source_lots": session.scalar(
                select(func.count()).select_from(SourceLot).where(
                    SourceLot.is_active.is_(True), SourceLot.is_archived.is_(True)
                )
            ) or 0,
            "archived_processed_without_timestamp": session.scalar(
                select(func.count()).select_from(ProcessedLot).where(
                    ProcessedLot.is_archived.is_(True), ProcessedLot.archived_at.is_(None)
                )
            ) or 0,
            "current_dataset_count": current_dataset_count,
            "tile_count_mismatches": tile_count_mismatches,
        },
        "current_map_dataset": ({
            "version": current.version,
            "status": current.status,
            "point_count": current.point_count,
            "tile_count": current.tile_count,
            "published_at": current.published_at,
        } if current else None),
        "last_sync": ({
            "id": last_run.id,
            "status": last_run.status,
            "started_at": last_run.started_at,
            "finished_at": last_run.finished_at,
        } if last_run else None),
        "problems": {"stale_active_lots": stale_lots},
    }


_SOURCE_FRESH_SECONDS = 2 * 3600
_SOURCE_DELAYED_SECONDS = 6 * 3600
_SOURCE_COMPLETE_FRESH_SECONDS = 36 * 3600


def _source_age_seconds(now: Any, value: Any) -> int | None:
    if value is None:
        return None

    def naive_utc(item: Any) -> Any:
        if getattr(item, "tzinfo", None) is not None and item.utcoffset() is not None:
            return item.astimezone(timezone.utc).replace(tzinfo=None)
        return item.replace(tzinfo=None)

    return max(0, int((naive_utc(now) - naive_utc(value)).total_seconds()))


def _source_error_category(error: str | None) -> str | None:
    if not error:
        return None
    message = error.casefold()
    if any(
        marker in message
        for marker in (
            "uniqueviolation",
            "integrityerror",
            "duplicate key value violates unique constraint",
            "psycopg.errors",
        )
    ):
        return "database_integrity"
    if any(
        marker in message
        for marker in (
            "traceback (most recent call last)",
            "attributeerror",
            "typeerror",
            "keyerror",
        )
    ):
        return "internal_error"
    if "coverage guard" in message:
        return "coverage_guard"
    if "access_limited" in message:
        return "access_limited"
    if "429" in message or "rate limit" in message or "too many requests" in message:
        return "rate_limit"
    if any(marker in message for marker in ("401", "403", "unauthorized", "forbidden", "authentication")):
        return "authentication"
    if any(marker in message for marker in ("timeout", "timed out")):
        return "timeout"
    if any(marker in message for marker in ("502", "503", "504", "connection", "dns", "ssl", "tls", "network")):
        return "upstream_network"
    if any(marker in message for marker in ("validation", "invalid")):
        return "validation"
    return "upstream_error"


def list_source_health(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[SourceHealthDTO]:
    states = {row.source_system: row for row in session.scalars(select(SourceHealthState)).all()}
    counts = dict(
        session.execute(select(ProcessedLot.source_system, func.count()).group_by(ProcessedLot.source_system)).all()
    )
    run_names = set(session.scalars(select(LotSyncSourceRun.source_system).distinct()).all())
    names = sorted(set(states) | set(counts) | run_names)
    now = now or utc_now()

    values: list[SourceHealthDTO] = []
    for name in names:
        latest = session.scalar(
            select(LotSyncSourceRun)
            .where(LotSyncSourceRun.source_system == name)
            .order_by(LotSyncSourceRun.started_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_success = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "success",
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_complete = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "success",
                LotSyncSourceRun.complete_source_run.is_(True),
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_failure = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "failed",
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        state = states.get(name)
        last_success_at = (
            latest_success.finished_at
            if latest_success is not None
            else state.last_success_at if state is not None else None
        )
        last_failure_at = (
            latest_failure.finished_at
            if latest_failure is not None
            else state.last_failure_at if state is not None else None
        )
        last_error = (
            latest_failure.error_message
            if latest_failure is not None
            else state.last_error if state is not None else None
        )
        freshness_age = _source_age_seconds(now, last_success_at)
        complete_age = _source_age_seconds(
            now,
            latest_complete.finished_at if latest_complete is not None else None,
        )

        if latest is not None and latest.status in {"running", "queued"}:
            freshness_status = "running"
        elif latest is not None and latest.status == "failed":
            freshness_status = "failed"
        elif freshness_age is None:
            freshness_status = "stale"
        elif freshness_age <= _SOURCE_FRESH_SECONDS:
            freshness_status = "fresh"
        elif freshness_age <= _SOURCE_DELAYED_SECONDS:
            freshness_status = "delayed"
        else:
            freshness_status = "stale"

        if complete_age is None:
            coverage_status = "missing"
        elif complete_age <= _SOURCE_COMPLETE_FRESH_SECONDS:
            coverage_status = "fresh"
        else:
            coverage_status = "stale"

        if latest is not None:
            status = (
                "healthy"
                if latest.status == "success" and latest.complete_source_run
                else "partial"
                if latest.status == "success"
                else latest.status
            )
        else:
            status = state.status if state is not None else "not_checked"

        values.append(
            SourceHealthDTO(
                source_system=name,
                status=status,
                items_seen=(
                    latest.items_seen
                    if latest is not None
                    else state.items_seen if state is not None else int(counts.get(name, 0))
                ),
                last_attempt_at=(
                    latest.started_at
                    if latest is not None
                    else state.last_started_at if state is not None else None
                ),
                last_success_at=last_success_at,
                last_complete_success_at=latest_complete.finished_at if latest_complete is not None else None,
                last_failure_at=last_failure_at,
                last_error=last_error,
                last_error_category=_source_error_category(last_error),
                freshness_status=freshness_status,
                coverage_status=coverage_status,
                freshness_age_seconds=freshness_age,
                complete_snapshot_age_seconds=complete_age,
                last_duration_ms=latest.duration_ms if latest is not None else None,
                last_complete_source_run=bool(latest.complete_source_run) if latest is not None else False,
                last_pages_scanned=int(latest.pages_scanned) if latest is not None else 0,
                last_items_inserted=int(latest.items_inserted) if latest is not None else 0,
                last_items_updated=int(latest.items_updated) if latest is not None else 0,
                last_items_unchanged=int(latest.items_unchanged) if latest is not None else 0,
                last_items_archived=int(latest.items_archived) if latest is not None else 0,
                last_items_failed=int(latest.items_failed) if latest is not None else 0,
            )
        )
    return values


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
