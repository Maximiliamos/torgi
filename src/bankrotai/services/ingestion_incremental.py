"""Incremental-detail and resumable-source helpers kept out of the core orchestrator."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from sqlalchemy import select

from bankrotai.db import LotSyncSourceRun, SourceLot, utc_now
from bankrotai.services.incremental_detail_cache import snapshot_from_source_row


REUSABLE_DETAIL_SOURCES = frozenset({"m-ets.ru", "cdtrf.ru"})


def incremental_specs(specs: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(
        replace(spec, reuse_unchanged_details=spec.source_id in REUSABLE_DETAIL_SOURCES)
        for spec in specs
    )


def retry_specs(specs: tuple[Any, ...], source_id: str) -> tuple[Any, ...]:
    matched = tuple(spec for spec in specs if spec.source_id == source_id)
    if not matched:
        raise ValueError(f"Unsupported source retry: {source_id}")
    return tuple(
        replace(
            spec,
            reuse_unchanged_details=spec.source_id in REUSABLE_DETAIL_SOURCES,
            resume_from_checkpoint=True,
        )
        for spec in matched
    )


def detail_cache_snapshots(session_factory: Callable[[], Any], source_id: str) -> dict[str, dict[str, Any]]:
    with session_factory() as session:
        rows = session.scalars(
            select(SourceLot).where(
                SourceLot.source_system == source_id,
                SourceLot.is_archived.is_(False),
                SourceLot.processed_lot_id.isnot(None),
            )
        ).all()
        return {row.external_id: snapshot_from_source_row(row) for row in rows}


def resume_checkpoint_for_source(
    session_factory: Callable[[], Any],
    run_id: str,
    source_id: str,
) -> dict[str, Any] | None:
    with session_factory() as session:
        rows = session.scalars(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.sync_run_id != run_id,
                LotSyncSourceRun.source_system == source_id,
                LotSyncSourceRun.status.in_(("failed", "partial")),
                LotSyncSourceRun.checkpoint_json.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(5)
        ).all()
        for row in rows:
            checkpoint = row.checkpoint_json if isinstance(row.checkpoint_json, dict) else {}
            connector_checkpoint = checkpoint.get("connector_checkpoint")
            resume_cursor = checkpoint.get("resume_cursor")
            if isinstance(connector_checkpoint, dict) and isinstance(resume_cursor, str):
                return {
                    "connector_checkpoint": connector_checkpoint,
                    "resume_cursor": resume_cursor,
                    "pages_scanned": int(row.pages_scanned or 0),
                    "items_seen": int(row.items_seen or 0),
                    "items_inserted": int(row.items_inserted or 0),
                    "items_updated": int(row.items_updated or 0),
                    "items_unchanged": int(row.items_unchanged or 0),
                    "items_failed": int(row.items_failed or 0),
                    "detail_attempts": int(checkpoint.get("detail_attempts") or 0),
                    "detail_skipped_unchanged": int(checkpoint.get("detail_skipped_unchanged") or 0),
                    "retry_count": int(checkpoint.get("retry_count") or 0),
                }
    return None


def prepare_source(
    session_factory: Callable[[], Any],
    connector: Any,
    spec: Any,
    run_id: str,
    result: Any,
) -> str | None:
    if spec.reuse_unchanged_details:
        connector.configure_incremental_detail_cache(
            detail_cache_snapshots(session_factory, spec.source_id)
        )
    if not spec.resume_from_checkpoint:
        return None
    resume = resume_checkpoint_for_source(session_factory, run_id, spec.source_id)
    if resume is None:
        return None
    restored = connector.restore_resume_checkpoint(resume["connector_checkpoint"])
    if restored != resume["resume_cursor"]:
        return None
    result.resume_cursor = restored
    result.connector_checkpoint = resume["connector_checkpoint"]
    for field in (
        "pages_scanned", "items_seen", "items_inserted", "items_updated",
        "items_unchanged", "items_failed", "detail_attempts",
        "detail_skipped_unchanged", "retry_count",
    ):
        setattr(result, field, resume[field])
    result.phase = "resumed"
    return restored


def apply_page_metadata(result: Any, metadata: dict[str, Any], next_cursor: str | None) -> None:
    result.detail_attempts += int(metadata.get("detail_attempts") or 0)
    result.detail_skipped_unchanged += int(metadata.get("detail_skipped_unchanged") or 0)
    result.retry_count += int(metadata.get("retry_count") or 0)
    result.resume_cursor = next_cursor
    checkpoint = metadata.get("resume_checkpoint")
    result.connector_checkpoint = checkpoint if isinstance(checkpoint, dict) else None


def mark_reused_rows(
    session_factory: Callable[[], Any],
    run_id: str,
    result: Any,
    metadata: dict[str, Any],
) -> None:
    raw_ids = metadata.get("reused_existing_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return
    fingerprints = metadata.get("reused_catalogue_fingerprints")
    fingerprint_by_id = fingerprints if isinstance(fingerprints, dict) else {}
    requested = [
        str(value) for value in raw_ids
        if isinstance(value, str) and value not in result.seen_external_ids
    ]
    if not requested:
        return
    now = utc_now()
    with session_factory() as session:
        rows = session.scalars(
            select(SourceLot).where(
                SourceLot.source_system == result.source_system,
                SourceLot.external_id.in_(requested),
                SourceLot.is_archived.is_(False),
                SourceLot.processed_lot_id.isnot(None),
            )
        ).all()
        by_id = {row.external_id: row for row in rows}
        missing = sorted(set(requested) - set(by_id))
        if missing:
            raise RuntimeError(
                f"{result.source_system}: incremental detail cache lost {len(missing)} persisted rows"
            )
        for external_id in requested:
            row = by_id[external_id]
            previous_seen = row.last_seen_at
            raw = dict(row.raw_data or {})
            if not raw.get("detail_verified_at") and previous_seen is not None:
                raw["detail_verified_at"] = previous_seen.isoformat()
            fingerprint = fingerprint_by_id.get(external_id)
            if isinstance(fingerprint, str) and len(fingerprint) == 64:
                raw["catalogue_fingerprint"] = fingerprint
            row.raw_data = raw
            row.last_sync_run_id = run_id
            row.last_seen_at = now
            row.missing_successful_runs = 0
            result.items_seen += 1
            result.items_unchanged += 1
            result.seen_external_ids.add(external_id)
        session.commit()


def update_eta(result: Any) -> None:
    current, total = result.progress_current, result.progress_total
    if not current or not total or current <= 0 or total <= current or result.elapsed_seconds <= 0:
        result.eta_seconds = 0.0 if current and total and current >= total else None
        return
    result.eta_seconds = max(0.0, result.elapsed_seconds / current * (total - current))
