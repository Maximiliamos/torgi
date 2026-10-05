from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
from celery.schedules import crontab
from celery.utils import uuid

from bankrotai.core import get_app_setting, get_region_sync_slug, get_settings
from bankrotai.db import (
    BackgroundTaskState,
    GeoFailure,
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
_MAP_DIRTY_SINCE_KEY = "bankrotai:map-dataset-dirty-since"
_MAP_DIRTY_LAST_CHANGE_KEY = "bankrotai:map-dataset-dirty-last-change"
_MAP_SOURCE_FINGERPRINT_KEY = "bankrotai:map-source-fingerprint"
_GEO_BATCH_LIMIT = settings.geo_batch_limit
_GEO_CONTINUATION_DELAY_SECONDS = 2
_GEO_CONTINUATION_MAX_BATCHES = 8
_MAP_PUBLICATION_QUIET_SECONDS = 180
_MAP_PUBLICATION_MIN_INTERVAL_SECONDS = 600
_MAP_PUBLICATION_MAX_DELAY_SECONDS = 900
_FAST_NATIONWIDE_REFRESH_SECONDS = 900
_FULL_NATIONWIDE_REFRESH_SECONDS = 86_400
_NATIONWIDE_REFRESH_MAX_RETRIES = 3
_NATIONWIDE_BUSY_RETRY_MAX_RETRIES = 3
_NATIONWIDE_BUSY_RETRY_SECONDS = 300
_PARTIAL_SOURCE_RETRY_DELAY_SECONDS = 60
_DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS = max(
    int(settings.celery_nationwide_soft_time_limit),
    4 * 60 * 60,
)
_DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS = max(
    int(settings.celery_nationwide_hard_time_limit),
    _DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS + 60 * 60,
)
_SOURCE_PAUSE_SETTING_PREFIX = "source_paused:"
_QUEUE_INGESTION = "ingestion"
_QUEUE_GEOCODING = "geocoding"
_QUEUE_MAP = "map"
_QUEUE_MAINTENANCE = "maintenance"
_P7_TASK_SOFT_TIME_LIMIT_SECONDS = 4 * 60 * 60
_P7_TASK_HARD_TIME_LIMIT_SECONDS = 5 * 60 * 60
_P7_STALE_GRACE_SECONDS = 15 * 60
_P7_RUNNING_STALE_AFTER = timedelta(
    seconds=_P7_TASK_HARD_TIME_LIMIT_SECONDS + _P7_STALE_GRACE_SECONDS
)
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
        "bankrotai.tasks.automatic_nationwide_lot_refresh_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.automatic_nationwide_source_retry_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.sync_public_region_task": {"queue": _QUEUE_INGESTION},
        "bankrotai.tasks.geocode_pending_lots_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.recover_ik12_geo_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.probe_geo_network_health_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.geo_fast_drain_task": {"queue": _QUEUE_GEOCODING},
        "bankrotai.tasks.build_map_dataset_task": {"queue": _QUEUE_MAP},
        "bankrotai.tasks.publish_dirty_map_dataset_task": {"queue": _QUEUE_MAP},
        "bankrotai.tasks.cleanup_old_map_datasets_task": {"queue": _QUEUE_MAP},
    },
    beat_schedule={
        "expire-ended-lots": {
            "task": "bankrotai.tasks.expire_ended_lots_task",
            "schedule": 60.0,
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
        "probe-geo-network-health": {
            "task": "bankrotai.tasks.probe_geo_network_health_task",
            "schedule": 60.0,
            "options": {"expires": 45},
        },
        "recalculate-public-offer-prices": {
            "task": "bankrotai.tasks.recalculate_public_offer_prices_task",
            "schedule": 300.0,
            "options": {"expires": 240},
        },
        # Fast discovery reads one page per source and never reconciles missing
        # inventory. Keeping it on quarter-hour boundaries leaves the daily full
        # run an intentional offset, rather than asking both jobs to acquire the
        # durable lease at the same instant.
        "refresh-nationwide-sources-fast": {
            "task": "bankrotai.tasks.automatic_nationwide_lot_refresh_task",
            "schedule": crontab(minute="0,15,30,45"),
            "args": ("fast",),
            "options": {"expires": 840},
        },
        "refresh-nationwide-sources-full": {
            "task": "bankrotai.tasks.automatic_nationwide_lot_refresh_task",
            # The daily full run is bounded by the ingestion task's production
            # time limit and is the only cadence allowed to archive.
            "schedule": crontab(hour=3, minute=7),
            "args": ("full",),
            "options": {"expires": 3_600},
        },
        "publish-dirty-map-dataset": {
            "task": "bankrotai.tasks.publish_dirty_map_dataset_task",
            "schedule": crontab(minute="*"),
            "options": {"expires": 55},
        },
        "cleanup-old-map-datasets": {
            "task": "bankrotai.tasks.cleanup_old_map_datasets_task",
            "schedule": crontab(hour=4, minute=37),
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


def _p7_campaign_is_stale(state: BackgroundTaskState, *, now: datetime | None = None) -> bool:
    """Return True only when a running P7 row outlived the Celery hard limit plus grace."""
    if state.status != "running":
        return False
    reference = state.started_at or state.created_at
    if reference is None:
        return False
    current = now or _utc_now()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    if reference.tzinfo is not None:
        reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
    return current - reference >= _P7_RUNNING_STALE_AFTER


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
            _mark_map_dirty(client)
            client.close()
        except Exception:
            logger.exception("Could not mark map dataset dirty after public-offer price update")
    return result


@celery_app.task(bind=True, name="bankrotai.tasks.geocode_pending_lots_task")
def geocode_pending_lots_task(self, continuation_depth: int = 0) -> dict[str, Any]:
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
            _mark_map_dirty(client)
            client.close()
            result["map_dataset_build"] = {
                "status": "dirty",
                "quiet_seconds": _MAP_PUBLICATION_QUIET_SECONDS,
                "maximum_delay_seconds": _MAP_PUBLICATION_MAX_DELAY_SECONDS,
            }
        except Exception as exc:
            logger.exception("Could not mark map dataset dirty")
            result["map_dataset_build"] = {"status": "dirty_mark_failed", "error": str(exc)[:500]}
    if result.get("queued", 0) >= _GEO_BATCH_LIMIT and result.get("processed", 0) >= _GEO_BATCH_LIMIT:
        next_depth = max(0, int(continuation_depth)) + 1
        if next_depth < _GEO_CONTINUATION_MAX_BATCHES:
            result["continuation"] = _schedule_geocode_continuation(next_depth)
        else:
            result["continuation"] = {
                "status": "bounded_stop",
                "completed_batches": next_depth,
                "max_batches": _GEO_CONTINUATION_MAX_BATCHES,
                "resume": "celery-beat",
            }
    return result


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.geo_fast_drain_task",
    soft_time_limit=_P7_TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_P7_TASK_HARD_TIME_LIMIT_SECONDS,
)
def geo_fast_drain_task(self) -> dict[str, Any]:
    """Run the one-time P7 historical backlog migration and bounded fast drain."""
    from bankrotai.services.geo_fast_drain import run_geo_fast_drain

    task_id = str(self.request.id or uuid())
    return run_geo_fast_drain(SessionLocal, task_id=task_id)


def schedule_geo_fast_drain() -> str:
    """Queue one P7 campaign, recovering a stale running row before rescheduling."""
    if not broker_is_available():
        raise QueueUnavailableError("Background task queue is unavailable")
    with session_scope() as session:
        existing = (
            session.query(BackgroundTaskState)
            .filter(
                BackgroundTaskState.task_type == "geocoding_fast_drain",
                BackgroundTaskState.status.in_(("queued", "running")),
            )
            .order_by(BackgroundTaskState.created_at.desc())
            .first()
        )
        if existing is not None and not _p7_campaign_is_stale(existing):
            return existing.task_id
        if existing is not None:
            recovered_at = _utc_now()
            existing.status = "failed"
            existing.finished_at = recovered_at
            existing.error_message = (
                "Recovered stale P7 campaign before reschedule: running state exceeded "
                f"{int(_P7_RUNNING_STALE_AFTER.total_seconds())} seconds"
            )
            progress = dict(existing.progress_json or {})
            progress["phase"] = "stale_recovered"
            progress["stale_recovered_at"] = recovered_at.isoformat()
            existing.progress_json = progress
            session.flush()

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        task_id = f"geo-{stamp}-p7-{uuid()[:8]}"
        session.add(
            BackgroundTaskState(
                task_id=task_id,
                task_type="geocoding_fast_drain",
                status="queued",
                progress_json={"phase": "queued"},
            )
        )
    try:
        geo_fast_drain_task.apply_async(task_id=task_id)
    except Exception as exc:
        with session_scope() as session:
            state = session.query(BackgroundTaskState).filter_by(task_id=task_id).one_or_none()
            if state is not None:
                state.status = "failed"
                state.error_message = str(exc)[:2000]
                state.finished_at = _utc_now()
        raise QueueUnavailableError("Background task dispatch failed") from exc
    return task_id


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
            _mark_map_dirty(client)
            client.close()
            result["map_dataset_build"] = {
                "status": "dirty",
                "quiet_seconds": _MAP_PUBLICATION_QUIET_SECONDS,
                "maximum_delay_seconds": _MAP_PUBLICATION_MAX_DELAY_SECONDS,
            }
        except Exception as exc:
            logger.exception("Could not mark map dataset dirty after IK12 recovery")
            result["map_dataset_build"] = {
                "status": "dirty_mark_failed",
                "error": str(exc)[:500],
            }
    return result



@celery_app.task(name="bankrotai.tasks.probe_geo_network_health_task")
def probe_geo_network_health_task() -> dict[str, Any]:
    """Release only waits whose own provider has demonstrably recovered."""
    from bankrotai.services.geo_resilience import (
        probe_geo_network_health,
        provider_recovered_since,
    )

    snapshot = probe_geo_network_health()
    released = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with session_scope() as session:
        failures = (
            session.query(GeoFailure)
            .filter(GeoFailure.status == "network_wait")
            .all()
        )
        for failure in failures:
            try:
                payload = json.loads(failure.error_message or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            attempts = payload.get("attempts") if isinstance(payload, dict) else None
            provider = next(
                (
                    str(item.get("source"))
                    for item in reversed(attempts or [])
                    if isinstance(item, dict)
                    and item.get("operational")
                    and item.get("source")
                ),
                None,
            )
            if provider and provider_recovered_since(provider, failure.last_failed_at):
                failure.next_retry_at = now
                released += 1
    snapshot["released_network_wait"] = released
    return snapshot

def _schedule_geocode_continuation(continuation_depth: int) -> dict[str, str | int]:
    """Drain a bounded campaign while beat remains the long-term recovery watchdog."""
    try:
        queued = geocode_pending_lots_task.apply_async(
            kwargs={"continuation_depth": int(continuation_depth)},
            countdown=_GEO_CONTINUATION_DELAY_SECONDS,
        )
        return {
            "status": "queued",
            "task_id": str(queued.id),
            "countdown_seconds": _GEO_CONTINUATION_DELAY_SECONDS,
            "continuation_depth": int(continuation_depth),
            "max_batches": _GEO_CONTINUATION_MAX_BATCHES,
        }
    except Exception as exc:
        logger.exception("Could not schedule the next geocoding batch")
        return {"status": "schedule_failed", "error": str(exc)[:500]}


def _mark_map_dirty(client) -> None:
    """Record a map-affecting change without queueing duplicate build tasks."""
    now = int(time.time())
    pipe = client.pipeline(transaction=True)
    pipe.set(_MAP_DIRTY_KEY, "1")
    pipe.setnx(_MAP_DIRTY_SINCE_KEY, str(now))
    pipe.set(_MAP_DIRTY_LAST_CHANGE_KEY, str(now))
    pipe.execute()


def _redis_timestamp(client, key: str, fallback: int) -> int:
    raw = client.get(key)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


@celery_app.task(name="bankrotai.tasks.publish_dirty_map_dataset_task")
def publish_dirty_map_dataset_task() -> dict[str, Any]:
    """Publish only after a quiet window, with bounded freshness and cadence."""
    from redis import Redis
    from sqlalchemy import select

    from bankrotai.db import MapDataset

    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        if not client.get(_MAP_DIRTY_KEY):
            return {"status": "skipped", "reason": "map-not-dirty"}

        now = int(time.time())
        dirty_since = _redis_timestamp(client, _MAP_DIRTY_SINCE_KEY, now)
        last_change = _redis_timestamp(client, _MAP_DIRTY_LAST_CHANGE_KEY, dirty_since)
        with SessionLocal() as session:
            current_published_at = session.scalar(
                select(MapDataset.published_at)
                .where(MapDataset.is_current.is_(True), MapDataset.status == "ready")
            )
        last_publication = int(current_published_at.timestamp()) if current_published_at is not None else 0

        quiet_due = last_change + _MAP_PUBLICATION_QUIET_SECONDS
        freshness_deadline = dirty_since + _MAP_PUBLICATION_MAX_DELAY_SECONDS
        cadence_due = last_publication + _MAP_PUBLICATION_MIN_INTERVAL_SECONDS
        due_at = max(cadence_due, min(quiet_due, freshness_deadline))
        if now < due_at:
            return {
                "status": "deferred",
                "reason": "debounce-window",
                "retry_after_seconds": max(1, due_at - now),
                "quiet_seconds": _MAP_PUBLICATION_QUIET_SECONDS,
                "minimum_interval_seconds": _MAP_PUBLICATION_MIN_INTERVAL_SECONDS,
                "maximum_delay_seconds": _MAP_PUBLICATION_MAX_DELAY_SECONDS,
            }

        # Consume the dirty generation atomically. A change that lands after
        # this script sets the keys again and is picked up by the next minute
        # watchdog tick, so no update is lost while a build is running.
        consumed = client.eval(
            "local d=redis.call('GET',KEYS[1]);"
            "local s=redis.call('GET',KEYS[2]);"
            "local l=redis.call('GET',KEYS[3]);"
            "redis.call('DEL',KEYS[1],KEYS[2],KEYS[3]);"
            "return {d,s,l}",
            3,
            _MAP_DIRTY_KEY,
            _MAP_DIRTY_SINCE_KEY,
            _MAP_DIRTY_LAST_CHANGE_KEY,
        )
        if not consumed or not consumed[0]:
            return {"status": "skipped", "reason": "map-dirty-generation-already-consumed"}

        result = _schedule_map_dataset_build()
        if result.get("status") != "queued":
            _mark_map_dirty(client)
        return result
    finally:
        client.close()


@celery_app.task(name="bankrotai.tasks.cleanup_old_map_datasets_task")
def cleanup_old_map_datasets_task() -> dict[str, Any]:
    """Apply DB + S3 manifest retention while preserving current and rollback versions."""
    from bankrotai.services.map_builder import cleanup_map_datasets
    from bankrotai.services.map_object_store import delete_retired_dataset_manifests

    plan = cleanup_map_datasets(
        SessionLocal,
        retain_previous_ready=2,
        min_age_hours=1,
        building_min_age_hours=6,
        apply=False,
    )
    manifest_retention = delete_retired_dataset_manifests(
        list(plan.get("candidate_versions") or []),
    )
    applied = cleanup_map_datasets(
        SessionLocal,
        retain_previous_ready=2,
        min_age_hours=1,
        building_min_age_hours=6,
        apply=True,
    )
    return {
        **applied,
        "planned_candidate_dataset_count": int(plan.get("candidate_dataset_count") or 0),
        "manifest_retention": manifest_retention,
    }


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

    from sqlalchemy import select

    from bankrotai.db import MapDataset
    from bankrotai.services.map_builder import build_map_dataset, map_source_fingerprint
    from bankrotai.services.map_dataset_version import dataset_matches_current_pipeline

    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    lock = client.lock("bankrotai:map-dataset-build", timeout=1800, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        client.close()
        return {"status": "skipped", "reason": "build-already-running"}
    try:
        with SessionLocal() as session:
            source_fingerprint = map_source_fingerprint(session)
            current_version = session.scalar(
                select(MapDataset.version)
                .where(MapDataset.is_current.is_(True), MapDataset.status == "ready")
            )
        previous_fingerprint = client.get(_MAP_SOURCE_FINGERPRINT_KEY)
        if previous_fingerprint is not None:
            previous_fingerprint = (
                previous_fingerprint.decode("utf-8")
                if isinstance(previous_fingerprint, bytes)
                else str(previous_fingerprint)
            )
        current_pipeline_matches = bool(
            current_version
            and dataset_matches_current_pipeline(
                current_version,
                object_store_enabled=settings.map_object_store_enabled,
                object_store_layout=settings.map_object_store_layout,
            )
        )
        if previous_fingerprint == source_fingerprint and current_pipeline_matches:
            return {
                "status": "skipped",
                "reason": "map-source-unchanged",
                "source_fingerprint": source_fingerprint,
                "current_version": current_version,
            }

        result = build_map_dataset(SessionLocal)
        if result.get("promotion_status") == "published":
            client.set(_MAP_SOURCE_FINGERPRINT_KEY, source_fingerprint)
            try:
                cleanup_old_map_datasets_task.delay()
                result["retention"] = {"status": "queued"}
            except Exception as exc:
                logger.exception("Could not queue map retention after publication")
                result["retention"] = {"status": "schedule_failed", "error": str(exc)[:500]}
        return {"status": str(result.get("status") or "published"), **result}
    finally:
        try:
            lock.release()
        except Exception:
            logger.warning("Map dataset lock expired before release")
        client.close()


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


def _sync_changed_map_membership(result: dict[str, Any]) -> bool:
    """Return whether an ingestion result can have changed public map content."""
    if int(result.get("expired_after_auction") or 0) > 0:
        return True
    changed_fields = ("items_inserted", "items_updated", "items_archived", "duplicates_merged")
    return any(
        isinstance(source, dict) and any(int(source.get(field) or 0) > 0 for field in changed_fields)
        for source in result.get("sources", [])
    )


def _source_is_paused(source_system: str) -> bool:
    # TBankrot is intentionally isolated behind interactive authentication.
    # A fresh/restored database must never add it to automatic nationwide runs
    # merely because the pause setting has not been persisted yet.
    default = "true" if source_system == "tbankrot.ru" else "false"
    value = get_app_setting(f"{_SOURCE_PAUSE_SETTING_PREFIX}{source_system}", default)
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _unpaused_source_specs(specs: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(spec for spec in specs if not _source_is_paused(str(spec.source_id)))


def _failed_source_systems(result: dict[str, Any]) -> tuple[str, ...]:
    """Return each failed source once, preserving the ingestion result order."""
    failed: list[str] = []
    for source in result.get("sources", []):
        if not isinstance(source, dict) or source.get("status") != "failed":
            continue
        source_system = source.get("source_system")
        if isinstance(source_system, str) and source_system and source_system not in failed:
            failed.append(source_system)
    return tuple(failed)


def _schedule_partial_source_retries(
    result: dict[str, Any],
    *,
    source_mode: str,
) -> list[dict[str, str | int]]:
    """Retry failed sources in the originating scope; successful peers stay out."""
    scheduled: list[dict[str, str | int]] = []
    for source_system in _failed_source_systems(result):
        if _source_is_paused(source_system):
            scheduled.append({"source_system": source_system, "status": "skipped", "reason": "source_paused"})
            continue
        try:
            queued = automatic_nationwide_source_retry_task.apply_async(
                args=[source_system, source_mode],
                countdown=_PARTIAL_SOURCE_RETRY_DELAY_SECONDS,
            )
            scheduled.append(
                {
                    "source_system": source_system,
                    "status": "queued",
                    "task_id": str(queued.id),
                    "countdown_seconds": _PARTIAL_SOURCE_RETRY_DELAY_SECONDS,
                }
            )
        except Exception as exc:
            logger.exception("Could not schedule targeted retry for source %s", source_system)
            scheduled.append(
                {
                    "source_system": source_system,
                    "status": "schedule_failed",
                    "error": str(exc)[:500],
                }
            )
    return scheduled


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
    soft_time_limit=_DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS,
)
def nationwide_lot_sync_task(self, run_id: str, mode: str = "full") -> dict:
    try:
        if mode == "fast":
            specs = _fast_nationwide_source_specs()
        elif mode == "full":
            specs = _unpaused_source_specs(default_source_specs())
        elif mode.startswith("source-fast:"):
            source_system = mode.removeprefix("source-fast:")
            specs = tuple(spec for spec in _fast_nationwide_source_specs() if spec.source_id == source_system)
            if not specs:
                raise ValueError(f"Unsupported source-only fast sync: {source_system}")
        elif mode.startswith("source:"):
            specs = source_full_specs(mode.removeprefix("source:"))
        else:
            raise ValueError(f"Unsupported nationwide sync mode: {mode}")
        result = run_nationwide_sync(SessionLocal, run_id, specs)
        if result.get("status") in {"success", "partial"}:
            if _sync_changed_map_membership(result):
                try:
                    from redis import Redis

                    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
                    _mark_map_dirty(client)
                    client.close()
                    result["map_dataset_build"] = {
                        "status": "dirty",
                        "quiet_seconds": _MAP_PUBLICATION_QUIET_SECONDS,
                        "maximum_delay_seconds": _MAP_PUBLICATION_MAX_DELAY_SECONDS,
                    }
                except Exception as exc:
                    logger.exception("Could not mark map dirty after nationwide sync")
                    result["map_dataset_build"] = {"status": "dirty_mark_failed", "error": str(exc)[:500]}
            else:
                result["map_dataset_build"] = {
                    "status": "skipped",
                    "reason": "no-map-affecting-source-changes",
                }
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


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.automatic_nationwide_lot_refresh_task",
    soft_time_limit=_DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS,
)
def automatic_nationwide_lot_refresh_task(self, mode: str) -> dict[str, Any]:
    """Run one beat-triggered nationwide refresh under the durable run lease."""
    if mode not in {"fast", "full"}:
        raise ValueError(f"Unsupported automatic nationwide sync mode: {mode}")
    return _run_automatic_nationwide_refresh(
        self,
        mode=mode,
        trigger_type=f"scheduled_{mode}",
        total_sources=len(_unpaused_source_specs(default_source_specs())),
        retry_if_busy=mode == "full",
    )


@celery_app.task(
    bind=True,
    name="bankrotai.tasks.automatic_nationwide_source_retry_task",
    soft_time_limit=_DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS,
)
def automatic_nationwide_source_retry_task(self, source_system: str, source_mode: str) -> dict[str, Any]:
    """Bound a retry of one failed source without re-running successful peers."""
    if _source_is_paused(source_system):
        return {"status": "skipped", "reason": "source_paused", "source_system": source_system}
    if source_mode == "fast":
        mode = f"source-fast:{source_system}"
        # Validation occurs in nationwide_lot_sync_task, after it computes the
        # same fast GIS overlap window as the originating run.
        total_sources = 1
    elif source_mode == "full":
        mode = f"source:{source_system}"
        total_sources = len(source_full_specs(source_system))
    else:
        raise ValueError(f"Unsupported source retry mode: {source_mode}")
    return _run_automatic_nationwide_refresh(
        self,
        mode=mode,
        trigger_type="scheduled_retry",
        total_sources=total_sources,
        retry_if_busy=True,
    )


def _run_automatic_nationwide_refresh(
    task: Any,
    *,
    mode: str,
    trigger_type: str,
    total_sources: int,
    retry_if_busy: bool,
) -> dict[str, Any]:
    """Create a durable run before work and preserve explicit retry diagnostics."""
    service = NationwideIngestionService(SessionLocal)
    try:
        run_id = service.create_run(
            triggered_by="celery-beat",
            trigger_type=trigger_type,
            total_sources=total_sources,
        )
    except SyncAlreadyRunningError as exc:
        if retry_if_busy:
            _retry_automatic_refresh(
                task,
                exc,
                max_retries=_NATIONWIDE_BUSY_RETRY_MAX_RETRIES,
                base_delay_seconds=_NATIONWIDE_BUSY_RETRY_SECONDS,
            )
        return {"status": "skipped", "reason": "already_running", "run_id": exc.run_id}

    try:
        result = nationwide_lot_sync_task.run(run_id, mode)
        if result.get("status") != "failed":
            response = {"run_id": run_id, **result}
            if result.get("status") == "partial":
                source_retries = _schedule_partial_source_retries(result, source_mode="fast" if mode == "fast" else "full")
                if source_retries:
                    response["targeted_source_retries"] = source_retries
            return response
        raise RuntimeError("Nationwide source refresh completed with failed status")
    except Exception as exc:
        _retry_automatic_refresh(task, exc)


def _retry_automatic_refresh(
    task: Any,
    exc: Exception,
    *,
    max_retries: int = _NATIONWIDE_REFRESH_MAX_RETRIES,
    base_delay_seconds: int = 60,
) -> NoReturn:
    if task.request.retries >= max_retries:
        raise exc
    countdown = min(900, base_delay_seconds * (2 ** task.request.retries))
    raise task.retry(exc=exc, countdown=countdown, max_retries=max_retries) from exc


def _fast_nationwide_source_specs() -> tuple[Any, ...]:
    """Build the bounded, non-reconciling source set used by fast retries too."""
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
    return _unpaused_source_specs(fast_source_specs(gis_publish_date_from=overlap_start.date().isoformat()))


def schedule_nationwide_lot_sync(*, triggered_by: str, mode: str = "fast") -> str:
    if mode not in {"fast", "full"} and not mode.startswith("source:"):
        raise ValueError(f"Unsupported nationwide sync mode: {mode}")
    is_source_only = mode.startswith("source:")
    specs = source_full_specs(mode.removeprefix("source:")) if is_source_only else _unpaused_source_specs(default_source_specs())
    if not broker_is_available():
        raise QueueUnavailableError("Background task queue is unavailable")
    service = NationwideIngestionService(SessionLocal)
    run_id = service.create_run(
        triggered_by=triggered_by,
        # LotSyncRun.trigger_type is a legacy VARCHAR(20). The source identity
        # is represented by LotSyncSourceRun, so keep this operational label
        # stable and within the existing schema limit.
        trigger_type="manual_source_full" if is_source_only else f"manual_{mode}",
        total_sources=len(specs),
    )
    try:
        nationwide_lot_sync_task.apply_async(
            args=[run_id, mode],
            task_id=run_id,
            soft_time_limit=_DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS,
            time_limit=_DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS,
        )
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
