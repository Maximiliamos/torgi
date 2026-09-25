from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils import uuid

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
    fast_source_specs,
    run_nationwide_sync,
    source_full_specs,
)
from bankrotai.scrapers import (
    TorgiGovClient,
    TorgiGovSearchFilters,
    ingest_recent_tbankrot,
    sync_public_real_estate,
)

logger = logging.getLogger(__name__)
settings = get_settings()
_MAP_DIRTY_KEY = "bankrotai:map-dataset-dirty"
_GEO_BATCH_LIMIT = settings.geo_batch_limit
_GEO_CONTINUATION_DELAY_SECONDS = 2
_MAP_PUBLICATION_DEBOUNCE_SECONDS = 60
_QUEUE_INGESTION = "ingestion"
_QUEUE_GEOCODING = "geocoding"
_QUEUE_MAP = "map"
_QUEUE_MAINTENANCE = "maintenance"
celery_app = Celery("bankrotai", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_soft_time_limit=settings.celery_soft_time_limit,
    task_time_limit=settings.celery_hard_time_limit,
    broker_connection_retry_on_startup=True,
    task_default_queue=_QUEUE_MAINTENANCE,
    task_routes={
        "bankrotai.tasks.bulk_torgi_gov_sync_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.nationwide_lot_sync_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.scheduled_nationwide_refresh_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.sync_public_region_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.geocode_pending_lots_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.recover_ik12_geo_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.build_map_dataset_task": {"queue": _QUEUE_MAP},
        "bankrotai.tasks.publish_dirty_map_dataset_task": {"queue": _QUEUE_MAP},
        "bankrotai.tasks.cleanup_old_map_datasets_task": {"queue": _QUEUE_MAP},
    },
    beat_schedule={
        "expire-ended-lots": {
            "task": "bankrotai.tasks.expire_ended_lots_task",
            "schedule": 60.0,
        },
        "nationwide-source-refresh": {
            "task": "bankrotai.tasks.scheduled_nationwide_refresh_task",
            "schedule": 3600.0,
            "options": {"expires": 3300},
        },
        "geocode-pending-lots": {
            "task": "bankrotai.tasks.geocode_pending_lots_task",
            "schedule": 300.0,
            "options": {"expires": 240},
        },
        "recover-ik12-cadastral-misses": {
            "task": "bankrotai.tasks.recover_ik12_geo_task",
            "schedule": 300.0,
            "options": {"expires": 240},
        },
        "recalculate-public-offer-prices": {
            "task": "bankrotai.tasks.recalculate_public_offer_prices_task",
            "schedule": 300.0,
            "options": {"expires": 240},
        },
        "publish-dirty-map-dataset": {
            "task": "bankrotai.tasks.publish_dirty_map_dataset_task",
            "schedule": 300.0,
            "options": {"expires": 240},
        },
        "cleanup-old-map-datasets": {
            "task": "bankrotai.tasks.cleanup_old_map_datasets_task",
            "schedule": 86400.0,
            "options": {"expires": 3600},
        },
        "daily-operational-quality-report": {
            "task": "bankrotai.tasks.daily_operational_quality_report_task",
            "schedule": 86400.0,
            "options": {"expires": 3600},
        },
    },
)


class QueueUnavailableError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@celery_app.task(name="bankrotai.tasks.expire_ended_lots_task")
def expire_ended_lots_task() -> dict[str, int]:
    service = NationwideIngestionService(SessionLocal)
    return {"archived": service._expire_elapsed_auctions()}


@celery_app.task(name="bankrotai.tasks.recalculate_public_offer_prices_task")
def recalculate_public_offer_prices_task() -> dict[str, int]:
    from bankrotai.services.price_schedule import recalculate_public_offer_prices

    result = recalculate_public_offer_prices(SessionLocal)
    if result["changed"]:
        try:
            from redis import Redis

            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.set(_MAP_DIRTY_KEY, "1")
            client.close()
        except Exception:
            logger.exception("Could not mark map dataset dirty after public-offer price update")
    return result


@celery_app.task(bind=True, name="bankrotai.tasks.geocode_pending_lots_task")
def geocode_pending_lots_task(self) -> dict[str, Any]:
    from bankrotai.services.geo_backfill import geocode_pending_lots

    task_id = str(self.request.id or uuid())
    result: dict[str, Any] = geocode_pending_lots(
        SessionLocal,
        limit=_GEO_BATCH_LIMIT,
        progress_task_id=f"celery-{task_id}",
    )
    if result.get("geocoded", 0):
        try:
            from redis import Redis

            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.set(_MAP_DIRTY_KEY, "1")
            client.close()
            result["map_dataset_build"] = _schedule_dirty_map_publication()
        except Exception as exc:
            logger.exception("Could not mark map dataset dirty")
            result["map_dataset_build"] = {"status": "dirty_mark_failed", "error": str(exc)[:500]}
    if result.get("queued", 0) >= _GEO_BATCH_LIMIT and result.get("processed", 0) >= _GEO_BATCH_LIMIT:
        result["continuation"] = _schedule_geocode_continuation()
    return result


@celery_app.task(bind=True, name="bankrotai.tasks.recover_ik12_geo_task")
def recover_ik12_geo_task(self) -> dict[str, Any]:
    from bankrotai.services.geo_backfill import run_ik12_recovery_batch

    task_id = str(self.request.id or uuid())
    result: dict[str, Any] = run_ik12_recovery_batch(
        SessionLocal,
        limit=5,
        progress_task_id=f"ik12-{task_id}",
    )
    if result.get("recovered", 0):
        try:
            from redis import Redis

            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.set(_MAP_DIRTY_KEY, "1")
            client.close()
            result["map_dataset_build"] = _schedule_dirty_map_publication()
        except Exception as exc:
            logger.exception("Could not mark map dataset dirty after IK12 recovery")
            result["map_dataset_build"] = {
                "status": "dirty_mark_failed",
                "error": str(exc)[:500],
            }
    return result


def _schedule_geocode_continuation() -> dict[str, str | int]:
    """Drain a backlog promptly while beat remains a recovery watchdog."""
    try:
        queued = geocode_pending_lots_task.apply_async(countdown=_GEO_CONTINUATION_DELAY_SECONDS)
        return {
            "status": "queued",
            "task_id": str(queued.id),
            "countdown_seconds": _GEO_CONTINUATION_DELAY_SECONDS,
        }
    except Exception as exc:
        logger.exception("Could not schedule the next geocoding batch")
        return {"status": "schedule_failed", "error": str(exc)[:500]}


def _schedule_dirty_map_publication() -> dict[str, str | int]:
    """Debounce publication without making completed geocoding fail."""
    try:
        queued = publish_dirty_map_dataset_task.apply_async(
            countdown=_MAP_PUBLICATION_DEBOUNCE_SECONDS,
        )
        return {
            "status": "deferred",
            "task_id": str(queued.id),
            "maximum_delay_seconds": _MAP_PUBLICATION_DEBOUNCE_SECONDS,
        }
    except Exception as exc:
        logger.exception("Could not schedule debounced map publication")
        return {
            "status": "deferred_to_watchdog",
            "maximum_delay_seconds": 300,
            "error": str(exc)[:500],
        }


@celery_app.task(name="bankrotai.tasks.publish_dirty_map_dataset_task")
def publish_dirty_map_dataset_task() -> dict[str, Any]:
    """Coalesce many geo batches into at most one map publication per interval."""
    from redis import Redis

    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    dirty = client.getdel(_MAP_DIRTY_KEY)
    if not dirty:
        client.close()
        return {"status": "skipped", "reason": "map-not-dirty"}
    result = _schedule_map_dataset_build()
    if result.get("status") != "queued":
        client.set(_MAP_DIRTY_KEY, "1")
    client.close()
    return result


@celery_app.task(name="bankrotai.tasks.cleanup_old_map_datasets_task")
def cleanup_old_map_datasets_task() -> dict[str, Any]:
    """Apply the tested retention policy while preserving current and rollback versions."""
    from bankrotai.services.map_builder import cleanup_map_datasets

    return cleanup_map_datasets(
        SessionLocal,
        retain_previous_ready=1,
        min_age_hours=24,
        apply=True,
    )


@celery_app.task(name="bankrotai.tasks.daily_operational_quality_report_task")
def daily_operational_quality_report_task() -> dict[str, Any]:
    from bankrotai.services.quality import operational_quality_report, record_diagnostic

    with SessionLocal() as session:
        report = operational_quality_report(session)
        record_diagnostic(
            session,
            severity="warning" if report["problems"]["stale_active_lots"] else "info",
            component="daily-quality-report",
            message="Daily source, geocoding, price and map quality report",
            context=report,
        )
        session.commit()
    return report


@celery_app.task(name="bankrotai.tasks.build_map_dataset_task")
def build_map_dataset_task() -> dict:
    """Publish a new immutable map version after source or geo changes."""
    from redis import Redis

    from bankrotai.services.map_builder import build_map_dataset

    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    lock = client.lock("bankrotai:map-dataset-build", timeout=1800, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "build-already-running"}
    try:
        return {"status": "published", **build_map_dataset(SessionLocal)}
    finally:
        try:
            lock.release()
        except Exception:
            logger.warning("Map dataset lock expired before release")


def _schedule_map_dataset_build() -> dict[str, str]:
    """Best-effort scheduling must never turn a completed data job into a failure."""
    try:
        queued = build_map_dataset_task.delay()
        return {"status": "queued", "task_id": str(queued.id)}
    except Exception as exc:
        logger.exception("Map dataset build scheduling failed; a later run or CLI build can recover it")
        return {"status": "schedule_failed", "error": str(exc)[:500]}


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


def _set_task_state(
    task_id: str, *, status: str, progress: dict | None = None, result: dict | None = None, error: str | None = None
) -> None:
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
            chunk = lots[offset : offset + 100]
            with session_scope() as session:
                for normalized in chunk:
                    from bankrotai.db import ProcessedLot

                    was_present = (
                        session.query(ProcessedLot.id)
                        .filter_by(
                            source_system=normalized.source_system,
                            external_id=normalized.external_id,
                        )
                        .first()
                        is not None
                    )
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
            countdown = settings.sync_retry_backoff_seconds * (2**self.request.retries)
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
            upsert_region_sync_state(session, city_slug, status="ready", lots_discovered=total, finished_at=_utc_now())
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
    task_id = uuid()
    # Persist the observable task row before publishing. A fast worker can start
    # immediately after apply_async(), so writing it afterwards races with the
    # worker's own running-state update and can fail the HTTP enqueue request.
    _set_task_state(task_id, status="queued", progress=_progress())
    try:
        bulk_torgi_gov_sync_task.apply_async(args=[filters_data, max_items], task_id=task_id)
    except Exception as exc:
        _set_task_state(task_id, status="failed", progress=_progress(), error=str(exc))
        raise QueueUnavailableError("Background task queue is unavailable") from exc
    return task_id


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.nationwide_lot_sync_task",
    soft_time_limit=settings.celery_soft_time_limit,
    time_limit=settings.celery_hard_time_limit,
)
def nationwide_lot_sync_task(self, run_id: str, mode: str = "full") -> dict:
    try:
        if mode == "fast":
            with session_scope() as session:
                latest_gis = (
                    session.query(LotSyncSourceRun)
                    .filter_by(
                        source_system="torgi.gov.ru",
                        status="success",
                        complete_source_run=True,
                    )
                    .order_by(LotSyncSourceRun.finished_at.desc())
                    .first()
                )
                overlap_start = (
                    latest_gis.finished_at if latest_gis and latest_gis.finished_at else datetime.now(timezone.utc)
                ) - timedelta(days=1)
            specs = fast_source_specs(gis_publish_date_from=overlap_start.date().isoformat())
        elif mode == "full":
            specs = default_source_specs()
        elif mode.startswith("source:"):
            specs = source_full_specs(mode.removeprefix("source:"))
        else:
            raise ValueError(f"Unsupported nationwide sync mode: {mode}")
        result = run_nationwide_sync(SessionLocal, run_id, specs)
        if result.get("status") in {"success", "partial"}:
            result["map_dataset_build"] = _schedule_map_dataset_build()
            try:
                with session_scope() as session:
                    run = session.get(LotSyncRun, run_id)
                    if run is not None:
                        run.result_json = result
            except Exception:
                logger.exception("Could not persist map build scheduling diagnostics for sync %s", run_id)
        return result
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
            source_runs = (
                session.query(LotSyncSourceRun)
                .filter_by(
                    sync_run_id=run_id,
                    status="running",
                )
                .all()
            )
            for source_run in source_runs:
                source_run.status = "failed"
                source_run.complete_source_run = False
                source_run.finished_at = _utc_now()
                source_run.error_message = error_message
        logger.exception("Nationwide lot sync %s failed", run_id)
        raise


_FULL_SYNC_MAX_AGE = timedelta(hours=30)


def _scheduled_nationwide_sync_mode() -> str:
    """Use cheap fast refreshes between periodic complete reconciliation runs."""
    required_sources = {spec.source_id for spec in default_source_specs()}
    cutoff = _utc_now() - _FULL_SYNC_MAX_AGE
    with session_scope() as session:
        rows = (
            session.query(LotSyncSourceRun.source_system)
            .filter(
                LotSyncSourceRun.source_system.in_(required_sources),
                LotSyncSourceRun.status == "success",
                LotSyncSourceRun.complete_source_run.is_(True),
                LotSyncSourceRun.finished_at.isnot(None),
                LotSyncSourceRun.finished_at >= cutoff,
            )
            .distinct()
            .all()
        )
    fresh_complete_sources = {str(row[0]) for row in rows}
    return "fast" if required_sources <= fresh_complete_sources else "full"


@celery_app.task(name="bankrotai.tasks.scheduled_nationwide_refresh_task")
def scheduled_nationwide_refresh_task() -> dict[str, Any]:
    mode = _scheduled_nationwide_sync_mode()
    try:
        run_id = schedule_nationwide_lot_sync(
            triggered_by="celery-beat",
            mode=mode,
            trigger_type=f"scheduled_{mode}",
        )
        return {"status": "queued", "mode": mode, "run_id": run_id}
    except SyncAlreadyRunningError as exc:
        return {"status": "busy", "mode": mode, "run_id": exc.run_id}
    except QueueUnavailableError as exc:
        return {"status": "queue_unavailable", "mode": mode, "error": str(exc)[:500]}


def schedule_nationwide_lot_sync(
    *,
    triggered_by: str,
    mode: str = "fast",
    trigger_type: str | None = None,
) -> str:
    if mode not in {"fast", "full"} and not mode.startswith("source:"):
        raise ValueError(f"Unsupported nationwide sync mode: {mode}")
    is_source_only = mode.startswith("source:")
    specs = source_full_specs(mode.removeprefix("source:")) if is_source_only else default_source_specs()
    if not broker_is_available():
        raise QueueUnavailableError("Background task queue is unavailable")
    service = NationwideIngestionService(SessionLocal)
    run_id = service.create_run(
        triggered_by=triggered_by,
        # LotSyncRun.trigger_type is a legacy VARCHAR(20). The source identity
        # is represented by LotSyncSourceRun, so keep this operational label
        # stable and within the existing schema limit.
        trigger_type=trigger_type or ("manual_source_full" if is_source_only else f"manual_{mode}"),
        total_sources=len(specs),
    )
    try:
        nationwide_lot_sync_task.apply_async(args=[run_id, mode], task_id=run_id)
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
