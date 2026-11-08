"""Incremental-detail and resumable-source helpers kept out of the core orchestrator."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
from typing import Any, Callable

from sqlalchemy import select

from bankrotai.db import LotSyncSourceRun, SourceLot, utc_now
from bankrotai.services.incremental_detail_cache import parse_timestamp, snapshot_from_source_row


REUSABLE_DETAIL_SOURCES = frozenset({"m-ets.ru", "cdtrf.ru"})
SAFE_RESUME_ERROR_CATEGORIES = frozenset({
    "http_429",
    "http_5xx",
    "dns",
    "tls",
    "connect_timeout",
    "read_timeout",
})


def filter_signature(spec: Any) -> str:
    """Stable crawl-contract signature used to reject stale/incompatible checkpoints."""
    payload = (
        spec.source_id,
        repr(spec.filters),
        spec.archive_region_code,
        spec.reconcile_missing,
        spec.max_batches,
        spec.require_source_end,
        spec.max_detail_attempts_per_page,
        spec.reuse_unchanged_details,
    )
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


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
    expected_filter_signature: str,
) -> dict[str, Any] | None:
    """Return only a recent, transport-retryable checkpoint for the same crawl contract."""
    from bankrotai.services.source_resilience import classify_source_error

    cutoff = utc_now() - timedelta(hours=24)
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
            .limit(10)
        ).all()
        for row in rows:
            observed_at = row.finished_at or row.progress_at or row.started_at
            if observed_at is None or observed_at < cutoff:
                continue
            classification = classify_source_error(row.error_message)
            if (
                not classification.retryable
                or classification.category not in SAFE_RESUME_ERROR_CATEGORIES
            ):
                continue
            checkpoint = row.checkpoint_json if isinstance(row.checkpoint_json, dict) else {}
            if checkpoint.get("filter_signature") != expected_filter_signature:
                continue
            connector_checkpoint = checkpoint.get("connector_checkpoint")
            resume_cursor = checkpoint.get("resume_cursor")
            if isinstance(connector_checkpoint, dict) and isinstance(resume_cursor, str):
                return {
                    "connector_checkpoint": connector_checkpoint,
                    "resume_cursor": resume_cursor,
                    "resumed_from_run_id": str(row.sync_run_id),
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
    result.filter_signature = filter_signature(spec)
    if spec.reuse_unchanged_details:
        connector.configure_incremental_detail_cache(
            detail_cache_snapshots(session_factory, spec.source_id)
        )
    if not spec.resume_from_checkpoint:
        return None
    resume = resume_checkpoint_for_source(
        session_factory, run_id, spec.source_id, result.filter_signature
    )
    if resume is None:
        return None
    restored = connector.restore_resume_checkpoint(resume["connector_checkpoint"])
    if restored != resume["resume_cursor"]:
        return None
    result.resume_cursor = restored
    result.connector_checkpoint = resume["connector_checkpoint"]
    result.resumed_from_run_id = resume["resumed_from_run_id"]
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
    result.catalogue_observations += max(0, int(metadata.get("catalogue_lots") or 0))
    property_candidates = (
        metadata["property_hint_candidates"]
        if "property_hint_candidates" in metadata
        else metadata.get("catalogue_lots")
    )
    result.property_hint_candidates += max(0, int(property_candidates or 0))
    result.verified_property_lots += max(0, int(metadata.get("verified_property_lots") or 0))
    result.details_rejected += max(0, int(metadata.get("details_rejected") or 0))
    # Persist only bounded machine-readable rejection categories, never raw
    # provider HTML/text or private lot metadata into the health checkpoint.
    rejection_codes = metadata.get("rejection_codes")
    if isinstance(rejection_codes, dict):
        for reason, count in rejection_codes.items():
            if (
                isinstance(reason, str)
                and reason.isascii() and 1 <= len(reason) <= 64
                and all(c.isalnum() or c == "_" for c in reason)
                and type(count) is int and count >= 0
            ):
                result.detail_rejection_reasons[reason] = (
                    result.detail_rejection_reasons.get(reason, 0) + count
                )
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
            raw = dict(row.raw_data or {})
            # Reuse evidence must be independent of last_seen_at. Never turn
            # catalogue observation into a fabricated detail verification.
            if parse_timestamp(raw.get("detail_verified_at")) is None:
                raise RuntimeError(
                    f"{result.source_system}: cached detail has no independent verification date"
                )
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
    discovery_seconds = float(result.timing_ms.get("catalogue_discovery_ms") or 0.0) / 1000.0
    active_seconds = max(0.001, result.elapsed_seconds - discovery_seconds)
    result.eta_seconds = max(0.0, active_seconds / current * (total - current))


def checkpoint_payload(result: Any) -> dict[str, Any]:
    return {
        "category_pages": result.category_pages,
        "current_category": result.current_category,
        "total_pages": result.total_pages,
        "progress_current": result.progress_current,
        "progress_total": result.progress_total,
        "phase": result.phase,
        "items_rejected": result.items_rejected,
        "quarantined_records": result.quarantined_records,
        "quarantine_reasons": result.quarantine_reasons,
        "quarantine_samples": result.quarantine_samples,
        "rows_per_second": round(result.items_seen / result.elapsed_seconds, 3)
        if result.elapsed_seconds else 0,
        "pages_per_second": round(result.pages_scanned / result.elapsed_seconds, 3)
        if result.elapsed_seconds else 0,
        "filter_signature": result.filter_signature,
        "resumed_from_run_id": result.resumed_from_run_id,
        "resume_cursor": result.resume_cursor,
        "connector_checkpoint": result.connector_checkpoint,
        "detail_attempts": result.detail_attempts,
        "detail_skipped_unchanged": result.detail_skipped_unchanged,
        "catalogue_observations": result.catalogue_observations,
        "property_hint_candidates": result.property_hint_candidates,
        "verified_property_lots": result.verified_property_lots,
        "details_rejected": result.details_rejected,
        "detail_rejection_reasons": result.detail_rejection_reasons,
        "detail_cache_hit_rate": round(
            result.detail_skipped_unchanged
            / max(1, result.detail_attempts + result.detail_skipped_unchanged),
            4,
        ),
        "retry_count": result.retry_count,
        "catalogue_discovery_ms": round(
            float(result.timing_ms.get("catalogue_discovery_ms") or 0.0), 3
        ),
        "eta_seconds": round(result.eta_seconds, 1)
        if result.eta_seconds is not None else None,
    }


def performance_profile(result: Any, profile_timings: bool) -> dict[str, Any]:
    return {
        **(
            {key: round(value, 3) for key, value in result.timing_ms.items()}
            if profile_timings else {}
        ),
        "sql_statements": result.sql_statements if profile_timings else 0,
        "http_requests": result.http_requests if profile_timings else 0,
        "response_bytes": result.response_bytes if profile_timings else 0,
        "sql_statements_per_100_lots": (
            round(result.sql_statements * 100 / result.items_seen, 2)
            if profile_timings and result.items_seen else 0
        ),
        "rows_per_second": round(result.items_seen / result.elapsed_seconds, 3)
        if result.elapsed_seconds else 0,
        "pages_per_second": round(result.pages_scanned / result.elapsed_seconds, 3)
        if result.elapsed_seconds else 0,
        "detail_attempts": result.detail_attempts,
        "detail_skipped_unchanged": result.detail_skipped_unchanged,
        "catalogue_observations": result.catalogue_observations,
        "property_hint_candidates": result.property_hint_candidates,
        "verified_property_lots": result.verified_property_lots,
        "details_rejected": result.details_rejected,
        "detail_rejection_reasons": result.detail_rejection_reasons,
        "detail_cache_hit_rate": round(
            result.detail_skipped_unchanged
            / max(1, result.detail_attempts + result.detail_skipped_unchanged),
            4,
        ),
        "retry_count": result.retry_count,
        "catalogue_discovery_ms": round(
            float(result.timing_ms.get("catalogue_discovery_ms") or 0.0), 3
        ),
        "eta_seconds": round(result.eta_seconds, 1)
        if result.eta_seconds is not None else None,
    }
