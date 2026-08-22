from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from bankrotai.core import get_region_sync_slug, get_settings
from bankrotai.db import (
    BackgroundTaskState,
    LotSyncRun,
    LotSyncSourceRun,
    SessionLocal,
    get_region_sync_state,
    init_db,
    session_scope,
    upsert_region_sync_state,
)
from bankrotai.logic import cleanup_closed_lots, persist_lot
from bankrotai.services.ingestion import (
    NationwideIngestionService,
    SyncAlreadyRunningError,
    default_source_specs,
    run_nationwide_sync,
)
from bankrotai.scrapers import (
    TorgiGovClient,
    TorgiGovSearchFilters,
    ingest_recent_tbankrot,
    sync_public_real_estate,
)

logger = logging.getLogger(__name__)
settings = get_settings()
celery_app = Celery("bankrotai", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_soft_time_limit=settings.celery_soft_time_limit,
    task_time_limit=settings.celery_hard_time_limit,
    broker_connection_retry_on_startup=True,
)


class QueueUnavailableError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _progress(**overrides: Any) -> dict[str, Any]:
    value = {
        "processed_pages": 0,
        "total_pages": None,
        "processed_items": 0,
        "saved_items": 0,
        "updated_items": 0,
        "skipped_items": 0,
        "errors": 0,
    }
    value.update(overrides)
    return value


def _set_task_state(task_id: str, *, status: str, progress: dict | None = None,
                    result: dict | None = None, error: str | None = None) -> None:
    init_db()
    with session_scope() as session:
        state = session.query(BackgroundTaskState).filter_by(task_id=task_id).one_or_none()
        if state is None:
            state = BackgroundTaskState(task_id=task_id, task_type="torgi_gov_bulk", status=status)
            session.add(state)
        state.status = status
        if progress is not None:
            state.progress_json = progress
        if result is not None:
            state.result_json = result
        state.error_message = error
        if status == "running" and state.started_at is None:
            state.started_at = _utc_now()
        if status in {"completed", "failed"}:
            state.finished_at = _utc_now()


def _is_transient_sync_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_markers = ("timeout", "timed out", "connection reset", "429", "502", "503", "504")
    permanent_markers = ("400", "401", "403", "validation", "invalid")
    return any(marker in message for marker in transient_markers) and not any(
        marker in message for marker in permanent_markers
    )


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.bulk_torgi_gov_sync_task",
    max_retries=settings.sync_retry_max_attempts,
)
def bulk_torgi_gov_sync_task(self, filters_data: dict, max_items: int = 10_000) -> dict:
    task_id = self.request.id or "local-bulk-sync"
    progress = _progress()
    _set_task_state(task_id, status="running", progress=progress)
    try:
        filters = TorgiGovSearchFilters(**filters_data)
        filters.page = 1
        filters.page_size = min(filters.page_size or 100, 100)
        client = TorgiGovClient()
        lots, meta = client.search_all_lots(filters, max_items=max_items)
        progress["processed_pages"] = int(meta.get("pages_loaded") or meta.get("page") or 0)
        progress["total_pages"] = meta.get("total_pages")
        progress["processed_items"] = len(lots)

        for offset in range(0, len(lots), 100):
            chunk = lots[offset:offset + 100]
            with session_scope() as session:
                for normalized in chunk:
                    from bankrotai.db import ProcessedLot
                    was_present = session.query(ProcessedLot.id).filter_by(
                        source_system=normalized.source_system,
                        external_id=normalized.external_id,
                    ).first() is not None
                    persist_lot(session, normalized)
                    progress["updated_items" if was_present else "saved_items"] += 1
            self.update_state(state="PROGRESS", meta=progress)
            _set_task_state(task_id, status="running", progress=progress)

        result = {**progress, "source_meta": meta}
        _set_task_state(task_id, status="completed", progress=progress, result=result)
        logger.info("Bulk torgi.gov sync %s completed: %s", task_id, progress)
        return result
    except SoftTimeLimitExceeded as exc:
        progress["errors"] += 1
        _set_task_state(task_id, status="failed", progress=progress, error="soft time limit exceeded")
        logger.exception("Bulk sync %s exceeded its soft time limit", task_id)
        raise RuntimeError("Bulk synchronization timed out") from exc
    except Exception as exc:
        progress["errors"] += 1
        if _is_transient_sync_error(exc) and self.request.retries < settings.sync_retry_max_attempts:
            _set_task_state(task_id, status="retrying", progress=progress, error=str(exc))
            countdown = settings.sync_retry_backoff_seconds * (2 ** self.request.retries)
            logger.warning("Retrying bulk sync %s in %ss after transient error: %s", task_id, countdown, exc)
            raise self.retry(exc=exc, countdown=countdown, max_retries=settings.sync_retry_max_attempts)
        _set_task_state(task_id, status="failed", progress=progress, error=str(exc))
        logger.exception("Bulk sync %s failed", task_id)
        raise


@celery_app.task(name="bankrotai.tasks.sync_public_region_task")
def sync_public_region_task(city_slug: str = "yaroslavl", force: bool = False, search: str | None = None) -> int:
    try:
        sync_slug = get_region_sync_slug(city_slug)
        init_db()
        with session_scope() as session:
            upsert_region_sync_state(session, city_slug, status="running", started_at=_utc_now())
        with session_scope() as session:
            imported_gt = sync_public_real_estate(session, sync_slug, search=search)
        with session_scope() as session:
            imported_tb = ingest_recent_tbankrot(session, sync_slug)
            cleanup_closed_lots(session)
            total = len(imported_gt) + len(imported_tb)
            upsert_region_sync_state(
                session, city_slug, status="ready", lots_discovered=total, finished_at=_utc_now()
            )
            return total
    except Exception as exc:
        logger.exception("Sync failed for %s", city_slug)
        with session_scope() as session:
            upsert_region_sync_state(session, city_slug, status="failed", error_message=str(exc))
        raise


def broker_is_available() -> bool:
    try:
        from redis import Redis
        return bool(Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping())
    except Exception:
        return False


def schedule_bulk_torgi_sync(filters_data: dict, max_items: int) -> str:
    if not broker_is_available():
        raise QueueUnavailableError("Background task queue is unavailable")
    result = bulk_torgi_gov_sync_task.apply_async(args=[filters_data, max_items])
    _set_task_state(result.id, status="queued", progress=_progress())
    return result.id


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.nationwide_lot_sync_task",
    soft_time_limit=6_900,
    time_limit=7_200,
)
def nationwide_lot_sync_task(self, run_id: str) -> dict:
    try:
        return run_nationwide_sync(SessionLocal, run_id, default_source_specs())
    except Exception as exc:
        with session_scope() as session:
            error_message = str(exc) or exc.__class__.__name__
            run = session.get(LotSyncRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = _utc_now()
                run.heartbeat_at = _utc_now()
                run.lease_expires_at = None
                run.error_message = error_message
            source_runs = session.query(LotSyncSourceRun).filter_by(
                sync_run_id=run_id,
                status="running",
            ).all()
            for source_run in source_runs:
                source_run.status = "failed"
                source_run.complete_source_run = False
                source_run.finished_at = _utc_now()
                source_run.error_message = error_message
        logger.exception("Nationwide lot sync %s failed", run_id)
        raise


def schedule_nationwide_lot_sync(*, triggered_by: str) -> str:
    if not broker_is_available():
        raise QueueUnavailableError("Background task queue is unavailable")
    service = NationwideIngestionService(SessionLocal)
    run_id = service.create_run(
        triggered_by=triggered_by,
        trigger_type="manual",
        total_sources=len(default_source_specs()),
    )
    try:
        nationwide_lot_sync_task.apply_async(args=[run_id], task_id=run_id)
    except Exception as exc:
        with session_scope() as session:
            run = session.get(LotSyncRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = _utc_now()
                run.lease_expires_at = None
                run.error_message = "Queue dispatch failed"
        raise QueueUnavailableError("Background task dispatch failed") from exc
    return run_id


def schedule_region_sync(city_slug: str, force: bool = False, search: str | None = None) -> str:
    init_db()
    with session_scope() as session:
        state = get_region_sync_state(session, city_slug)
        if state and state.status in {"queued", "running"} and not force:
            from bankrotai.db import _region_sync_is_stuck
            if not _region_sync_is_stuck(state):
                return "skipped-already-running"

    if not broker_is_available():
        if not settings.allow_local_task_fallback:
            logger.error("Queue unavailable; refusing thread fallback outside explicit local desktop mode")
            raise QueueUnavailableError("Background task queue is unavailable")
        with session_scope() as session:
            upsert_region_sync_state(session, city_slug, status="queued", requested_at=_utc_now())
        thread = threading.Thread(
            target=sync_public_region_task.run,
            args=(city_slug, force, search),
            daemon=True,
            name=f"bankrotai-sync-{city_slug}",
        )
        thread.start()
        return "started-in-thread"

    result = sync_public_region_task.apply_async(args=[city_slug, force, search])
    with session_scope() as session:
        upsert_region_sync_state(
            session,
            city_slug,
            status="queued",
            requested_at=_utc_now(),
            metadata_json={"task_id": result.id},
        )
    return result.id
