"""Incremental catalogue-detail orchestration for nationwide ingestion."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from bankrotai.db import LotSyncSourceRun, utc_now


def filter_signature(spec: Any) -> str:
    payload = (
        spec.source_id,
        repr(spec.filters),
        spec.archive_region_code,
        spec.reconcile_missing,
        spec.max_batches,
        spec.require_source_end,
        spec.max_detail_attempts_per_page,
    )
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def configure_catalogue_selector(
    session_factory: Any,
    connector: Any,
    spec: Any,
) -> None:
    setter = getattr(connector, "set_catalogue_selector", None)
    if not callable(setter):
        return
    max_age_hours = int(getattr(spec.filters, "detail_max_age_hours", 168))

    def selector(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        from bankrotai.services.catalogue_detail_freshness import (
            select_catalogue_candidates,
        )

        with session_factory() as session:
            return select_catalogue_candidates(
                session,
                spec.source_id,
                candidates,
                max_age=timedelta(hours=max_age_hours),
            )

    setter(selector)


def apply_catalogue_reuse(
    session_factory: Any,
    run_id: str,
    result: Any,
    metadata: dict[str, Any],
) -> None:
    reused = metadata.get("catalogue_unchanged")
    if not reused:
        return
    if not isinstance(reused, list) or any(
        not isinstance(item, dict) for item in reused
    ):
        raise RuntimeError("catalogue detail reuse metadata is malformed")
    from bankrotai.services.catalogue_detail_freshness import (
        touch_unchanged_catalogue_rows,
    )

    with session_factory() as session:
        touched = touch_unchanged_catalogue_rows(
            session,
            result.source_system,
            reused,
            run_id=run_id,
        )
        if touched != len(reused):
            raise RuntimeError(
                "catalogue detail reuse lost persisted identity; fail closed"
            )
        session.commit()
    result.items_seen += touched
    result.items_unchanged += touched
    result.seen_external_ids.update(str(item["external_id"]) for item in reused)


async def restore_safe_checkpoint(
    session_factory: Any,
    run_id: str,
    result: Any,
    spec: Any,
    connector: Any,
) -> str | None:
    restore = getattr(connector, "restore_checkpoint", None)
    if not callable(restore):
        return None
    cutoff = utc_now() - timedelta(hours=24)
    with session_factory() as session:
        candidates = session.scalars(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.sync_run_id != run_id,
                LotSyncSourceRun.source_system == spec.source_id,
                LotSyncSourceRun.status.in_(("failed", "partial")),
                LotSyncSourceRun.finished_at.isnot(None),
                LotSyncSourceRun.finished_at >= cutoff,
            )
            .order_by(
                LotSyncSourceRun.finished_at.desc(),
                LotSyncSourceRun.id.desc(),
            )
            .limit(5)
        ).all()

    from bankrotai.services.source_resilience import classify_source_error

    retryable_categories = {
        "http_429",
        "http_5xx",
        "dns",
        "tls",
        "connect_timeout",
        "read_timeout",
    }
    for previous in candidates:
        classification = classify_source_error(previous.error_message)
        if classification.category not in retryable_categories:
            continue
        checkpoint = previous.checkpoint_json or {}
        if checkpoint.get("filter_signature") != result.filter_signature:
            continue
        cursor = checkpoint.get("resume_cursor")
        state = checkpoint.get("connector_checkpoint")
        if not isinstance(cursor, str) or not isinstance(state, dict):
            continue
        if not await restore(spec.filters, state):
            continue
        result.pages_scanned = int(previous.pages_scanned or 0)
        result.items_seen = int(previous.items_seen or 0)
        result.items_inserted = int(previous.items_inserted or 0)
        result.items_updated = int(previous.items_updated or 0)
        result.items_unchanged = int(previous.items_unchanged or 0)
        result.items_failed = int(previous.items_failed or 0)
        result.items_rejected = int(checkpoint.get("items_rejected") or 0)
        result.catalogue_candidates = int(
            checkpoint.get("catalogue_candidates") or 0
        )
        result.detail_attempts = int(checkpoint.get("detail_attempts") or 0)
        result.detail_requests_saved = int(
            checkpoint.get("detail_requests_saved") or 0
        )
        result.current_category = checkpoint.get("current_category")
        result.total_pages = checkpoint.get("total_pages")
        result.progress_current = checkpoint.get("progress_current")
        result.progress_total = checkpoint.get("progress_total")
        result.category_pages = dict(checkpoint.get("category_pages") or {})
        result.resume_cursor = cursor
        result.connector_checkpoint = state
        result.resumed_from_run_id = previous.sync_run_id
        result.retry_count = int(checkpoint.get("retry_count") or 0) + 1
        result.phase = "resumed"
        return cursor
    return None


async def prepare_source(
    session_factory: Any,
    run_id: str,
    result: Any,
    spec: Any,
    connector: Any,
) -> str | None:
    result.filter_signature = filter_signature(spec)
    configure_catalogue_selector(session_factory, connector, spec)
    if not spec.allow_checkpoint_resume:
        return None
    return await restore_safe_checkpoint(
        session_factory, run_id, result, spec, connector,
    )


def record_page(
    session_factory: Any,
    run_id: str,
    result: Any,
    connector: Any,
    metadata: dict[str, Any],
    next_cursor: str | None,
) -> None:
    result.catalogue_candidates += int(metadata.get("catalogue_candidates") or 0)
    result.detail_attempts += int(metadata.get("detail_attempts") or 0)
    result.detail_requests_saved += int(metadata.get("detail_requests_saved") or 0)
    result.retry_count += int(metadata.get("retry_count") or 0)
    apply_catalogue_reuse(session_factory, run_id, result, metadata)
    if result.progress_current is not None and result.progress_total:
        completed = max(1, result.progress_current)
        remaining = max(0, result.progress_total - result.progress_current)
        result.eta_seconds = result.elapsed_seconds / completed * remaining
    result.resume_cursor = next_cursor
    exporter = getattr(connector, "export_checkpoint", None)
    if callable(exporter):
        checkpoint = exporter()
        result.connector_checkpoint = (
            checkpoint if isinstance(checkpoint, dict) else None
        )


def checkpoint_fields(result: Any) -> dict[str, Any]:
    return {
        "rows_per_second": (
            round(result.items_seen / result.elapsed_seconds, 3)
            if result.elapsed_seconds else 0
        ),
        "eta_seconds": (
            round(result.eta_seconds, 1)
            if result.eta_seconds is not None else None
        ),
        "catalogue_candidates": result.catalogue_candidates,
        "detail_attempts": result.detail_attempts,
        "detail_requests_saved": result.detail_requests_saved,
        "retry_count": result.retry_count,
        "filter_signature": result.filter_signature,
        "resume_cursor": result.resume_cursor,
        "connector_checkpoint": result.connector_checkpoint,
        "resumed_from_run_id": result.resumed_from_run_id,
    }


def result_fields(result: Any) -> dict[str, Any]:
    return {
        "catalogue_candidates": result.catalogue_candidates,
        "detail_attempts": result.detail_attempts,
        "detail_requests_saved": result.detail_requests_saved,
        "retry_count": result.retry_count,
        "eta_seconds": (
            round(result.eta_seconds, 1)
            if result.eta_seconds is not None else None
        ),
        "resumed_from_run_id": result.resumed_from_run_id,
    }
