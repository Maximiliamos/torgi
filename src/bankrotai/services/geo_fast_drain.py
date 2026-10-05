from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import math
import time
from typing import Any

from redis import Redis
from sqlalchemy import and_, case, func, or_, select

from bankrotai.core import get_settings, utc_now
from bankrotai.db import BackgroundTaskState, GeoFailure, ProcessedLot
from bankrotai.services.geo_backfill import (
    geocode_pending_lots,
    geocoding_progress,
    is_geocoding_paused,
)
from bankrotai.services.geo_resilience import is_operational_category, network_health_snapshot
from bankrotai.services.map_bundle_store import CFO_REGION_CODES

P7_TASK_TYPE = "geocoding_fast_drain"
P7_HOLD_STATUS = "p7_queued"
P7_HOLD_DAYS = 14
P7_DEFAULT_WAVE_SIZE = 2_000
P7_DEFAULT_BATCH_LIMIT = 500
P7_MAX_BATCHES = 64
P7_MAX_RUNTIME_SECONDS = 3 * 60 * 60

_VALIDATION_REASONS = {
    "low_confidence",
    "coordinates_out_of_range",
    "city_distance_mismatch",
    "city_name_mismatch",
    "locality_name_mismatch",
    "region_bounds_mismatch",
    "region_cadastral_mismatch",
    "result_cadastral_region_mismatch",
    "address_cadastral_region_mismatch",
}
_NETWORK_MARKERS = (
    "timeout",
    "timed out",
    "dns",
    "resolve",
    "connection",
    "ssl",
    "tls",
    "429",
    "502",
    "503",
    "504",
    "circuit",
)


def _payload(error_message: str | None) -> dict[str, Any]:
    try:
        value = json.loads(error_message or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    if isinstance(value, dict):
        return value
    return {}


def _legacy_classification(error_message: str | None) -> str | None:
    """Classify only pre-P6 rows; already classified P6/P7 rows are left untouched."""
    payload = _payload(error_message)
    raw_meta = payload.get("meta")
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    if meta.get("p7_reclassified"):
        return None
    persisted = str(payload.get("classification") or "")
    if persisted in {"operational", "no_match", "validation", "internal"}:
        return None

    attempts = [item for item in payload.get("attempts", []) if isinstance(item, dict)]
    reasons = [str(item.get("reason") or "") for item in attempts if item.get("reason")]
    if any(bool(item.get("operational")) or is_operational_category(item.get("reason")) for item in attempts):
        return "operational"
    if any(
        reason in _VALIDATION_REASONS
        or reason.endswith("_mismatch")
        for reason in reasons
    ):
        return "validation"
    if reasons and all(reason == "no_coordinates" for reason in reasons):
        return "no_match"

    raw = str(error_message or "")
    error = str(payload.get("error") or raw)
    folded = f"{error} {raw}".casefold()
    if any(marker in folded for marker in _NETWORK_MARKERS):
        return "operational"
    if (
        not reasons
        and error in {
            "Geocoding chain returned no result",
            "No validated coordinates",
            "No validated geocoding result",
        }
    ):
        return "no_match"
    if "no validated" in folded or "no coordinates" in folded:
        return "no_match"
    return "internal"


def _active_unmapped_filters() -> tuple[Any, ...]:
    return (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        or_(ProcessedLot.current_geo_lat.is_(None), ProcessedLot.current_geo_lon.is_(None)),
        or_(ProcessedLot.cadastral_number.is_not(None), ProcessedLot.address.is_not(None)),
    )


def _campaign_state(
    session_factory: Callable[[], Any],
    task_id: str,
    *,
    status: str,
    progress: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with session_factory() as session:
        state = session.scalar(select(BackgroundTaskState).where(BackgroundTaskState.task_id == task_id))
        if state is None:
            state = BackgroundTaskState(task_id=task_id, task_type=P7_TASK_TYPE, status=status)
            session.add(state)
        state.status = status
        state.progress_json = progress
        state.result_json = result
        state.error_message = error
        state.started_at = state.started_at or utc_now()
        if status in {"completed", "failed"}:
            state.finished_at = utc_now()
        session.commit()


def geo_fast_drain_plan(session: Any) -> dict[str, Any]:
    categories: Counter[str] = Counter()
    legacy_total = 0
    cfo_legacy = 0
    rows = session.execute(
        select(
            GeoFailure.error_message,
            ProcessedLot.region_code,
        )
        .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
        .where(
            *_active_unmapped_filters(),
            GeoFailure.status.in_(("queued", "network_wait")),
        )
    ).all()
    for error_message, region_code in rows:
        category = _legacy_classification(error_message)
        if category is None:
            continue
        legacy_total += 1
        categories[category] += 1
        if str(region_code or "") in CFO_REGION_CODES:
            cfo_legacy += 1

    held = int(
        session.scalar(
            select(func.count())
            .select_from(GeoFailure)
            .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
            .where(*_active_unmapped_filters(), GeoFailure.status == P7_HOLD_STATUS)
        )
        or 0
    )
    return {
        "legacy_total": legacy_total,
        "legacy_by_classification": dict(categories),
        "legacy_cfo": cfo_legacy,
        "p7_held": held,
    }


def reclassify_legacy_geo_backlog(session: Any, *, apply: bool) -> dict[str, Any]:
    """Move old multi-day retries into a held P7 queue without releasing a request storm."""
    now = utc_now()
    hold_until = now + timedelta(days=P7_HOLD_DAYS)
    rows = session.execute(
        select(GeoFailure, ProcessedLot.region_code)
        .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
        .where(
            *_active_unmapped_filters(),
            GeoFailure.status.in_(("queued", "network_wait")),
        )
        .order_by(
            case((ProcessedLot.region_code.in_(CFO_REGION_CODES), 0), else_=1),
            GeoFailure.last_failed_at.asc(),
        )
    ).all()

    categories: Counter[str] = Counter()
    changed = 0
    cfo = 0
    for failure, region_code in rows:
        category = _legacy_classification(failure.error_message)
        if category is None:
            continue
        categories[category] += 1
        if str(region_code or "") in CFO_REGION_CODES:
            cfo += 1
        if not apply:
            continue

        payload = _payload(failure.error_message)
        old_attempt_count = int(failure.attempt_count or 0)
        old_retry_at = failure.next_retry_at.isoformat() if failure.next_retry_at is not None else None
        raw_meta = payload.get("meta")
        meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        meta.update({
            "p7_reclassified": True,
            "legacy_attempt_count": old_attempt_count,
            "legacy_next_retry_at": old_retry_at,
            "reclassified_at": now.isoformat(),
        })
        payload["meta"] = meta
        payload["classification"] = category

        if category == "operational":
            attempts = [item for item in payload.get("attempts", []) if isinstance(item, dict)]
            if attempts:
                attempts[-1]["operational"] = True
            else:
                attempts = [{
                    "source": "legacy",
                    "valid": False,
                    "reason": "connection_error",
                    "operational": True,
                }]
            payload["attempts"] = attempts
            failure.status = "network_wait"
            failure.attempt_count = 0
            failure.next_retry_at = now + timedelta(minutes=1)
        else:
            # Old repeated semantic misses get exactly one fresh P7 pass. If the
            # modern resolver still misses, P6 immediately places them into the
            # appropriate deferred/terminal queue instead of another multi-day sleep.
            if category in {"no_match", "validation"}:
                failure.attempt_count = min(max(old_attempt_count, 1), 2)
            else:
                failure.attempt_count = min(max(old_attempt_count, 1), 3)
            failure.status = P7_HOLD_STATUS
            failure.next_retry_at = hold_until
        failure.error_message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:2000]
        failure.resolved_at = None
        changed += 1

    if apply:
        session.commit()
    return {
        "status": "applied" if apply else "planned",
        "legacy_total": sum(categories.values()),
        "legacy_by_classification": dict(categories),
        "legacy_cfo": cfo,
        "changed": changed,
        "hold_until": hold_until.isoformat() if apply else None,
    }


def _p7_counts(session: Any) -> dict[str, int]:
    now = utc_now()
    base = (
        *_active_unmapped_filters(),
        GeoFailure.status == P7_HOLD_STATUS,
    )
    total = int(
        session.scalar(
            select(func.count()).select_from(GeoFailure).join(
                ProcessedLot, ProcessedLot.id == GeoFailure.lot_id
            ).where(*base)
        ) or 0
    )
    due = int(
        session.scalar(
            select(func.count()).select_from(GeoFailure).join(
                ProcessedLot, ProcessedLot.id == GeoFailure.lot_id
            ).where(*base, or_(GeoFailure.next_retry_at.is_(None), GeoFailure.next_retry_at <= now))
        ) or 0
    )
    cfo = int(
        session.scalar(
            select(func.count()).select_from(GeoFailure).join(
                ProcessedLot, ProcessedLot.id == GeoFailure.lot_id
            ).where(*base, ProcessedLot.region_code.in_(CFO_REGION_CODES))
        ) or 0
    )
    return {"p7_total": total, "p7_due": due, "p7_held": max(0, total - due), "p7_cfo": cfo}


def release_geo_fast_drain_wave(session: Any, *, limit: int = P7_DEFAULT_WAVE_SIZE) -> dict[str, Any]:
    now = utc_now()
    rows = session.execute(
        select(GeoFailure, ProcessedLot.region_code)
        .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
        .where(
            *_active_unmapped_filters(),
            GeoFailure.status == P7_HOLD_STATUS,
            GeoFailure.next_retry_at > now,
        )
        .order_by(
            case((ProcessedLot.region_code.in_(CFO_REGION_CODES), 0), else_=1),
            GeoFailure.last_failed_at.asc(),
            GeoFailure.id.asc(),
        )
        .limit(max(1, int(limit)))
    ).all()
    cfo = 0
    for failure, region_code in rows:
        failure.next_retry_at = now
        if str(region_code or "") in CFO_REGION_CODES:
            cfo += 1
    session.commit()
    return {"released": len(rows), "released_cfo": cfo, **_p7_counts(session)}


def _mark_map_dirty() -> bool:
    try:
        client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.set("bankrotai:map-dataset-dirty", "1")
        client.close()
        return True
    except Exception:
        return False


def run_geo_fast_drain(
    session_factory: Callable[[], Any],
    *,
    task_id: str,
    wave_size: int = P7_DEFAULT_WAVE_SIZE,
    batch_limit: int = P7_DEFAULT_BATCH_LIMIT,
    max_batches: int = P7_MAX_BATCHES,
    max_runtime_seconds: int = P7_MAX_RUNTIME_SECONDS,
) -> dict[str, Any]:
    """Reclassify the legacy backlog and consume it in bounded CFO-first waves."""
    started = time.monotonic()
    aggregate: dict[str, Any] = {
        "status": "running",
        "phase": "planning",
        "batches": 0,
        "processed": 0,
        "geocoded": 0,
        "failed": 0,
        "provider_counts": {},
        "failure_reasons": {},
    }
    _campaign_state(session_factory, task_id, status="running", progress=aggregate)

    try:
        with session_factory() as session:
            aggregate["plan_before"] = geo_fast_drain_plan(session)
            aggregate["reclassification"] = reclassify_legacy_geo_backlog(session, apply=True)
            counts = _p7_counts(session)
        aggregate.update(counts)
        aggregate["phase"] = "draining"
        _campaign_state(session_factory, task_id, status="running", progress=aggregate)

        stop_reason = "completed"
        for batch_index in range(max(1, int(max_batches))):
            if time.monotonic() - started >= max_runtime_seconds:
                stop_reason = "runtime_limit"
                break
            with session_factory() as session:
                if is_geocoding_paused(session):
                    stop_reason = "paused"
                    break
                counts = _p7_counts(session)
                if counts["p7_due"] == 0 and counts["p7_held"] > 0:
                    released = release_geo_fast_drain_wave(session, limit=wave_size)
                    counts = {key: int(released[key]) for key in ("p7_total", "p7_due", "p7_held", "p7_cfo")}
                    aggregate["last_release"] = released

            if counts["p7_total"] == 0:
                stop_reason = "nothing_to_drain"
                break

            network = network_health_snapshot()
            if bool((network.get("external") or {}).get("circuit_open")):
                stop_reason = "external_network_degraded"
                break

            batch_task_id = f"{task_id}-batch-{batch_index + 1:03d}"
            result = geocode_pending_lots(
                session_factory,
                limit=max(1, min(int(batch_limit), 1000)),
                progress_task_id=batch_task_id,
            )
            aggregate["batches"] += 1
            aggregate["processed"] += int(result.get("processed") or 0)
            aggregate["geocoded"] += int(result.get("geocoded") or 0)
            aggregate["failed"] += int(result.get("failed") or 0)
            for provider, count in (result.get("provider_counts") or {}).items():
                aggregate["provider_counts"][provider] = aggregate["provider_counts"].get(provider, 0) + int(count or 0)
            for reason, count in (result.get("failure_reasons") or {}).items():
                aggregate["failure_reasons"][reason] = aggregate["failure_reasons"].get(reason, 0) + int(count or 0)

            with session_factory() as session:
                counts = _p7_counts(session)
            aggregate.update(counts)
            aggregate["latest_batch"] = {
                key: result.get(key)
                for key in (
                    "queued",
                    "processed",
                    "geocoded",
                    "failed",
                    "duration_seconds",
                    "lots_per_second",
                    "provider_counts",
                    "failure_reasons",
                )
            }
            aggregate["elapsed_seconds"] = round(time.monotonic() - started, 1)
            rate = aggregate["processed"] / max(float(aggregate["elapsed_seconds"]), 0.001)
            aggregate["rate_per_second"] = round(rate, 3)
            aggregate["estimated_p7_seconds"] = (
                math.ceil(counts["p7_total"] / rate) if rate > 0 and counts["p7_total"] > 0 else 0
            )
            _campaign_state(session_factory, task_id, status="running", progress=aggregate)

            if int(result.get("queued") or 0) == 0 and counts["p7_total"] > 0:
                stop_reason = "no_runnable_items"
                break

        aggregate["phase"] = "completed"
        aggregate["stop_reason"] = stop_reason
        aggregate["elapsed_seconds"] = round(time.monotonic() - started, 1)
        with session_factory() as session:
            aggregate.update(_p7_counts(session))
            aggregate["progress_after"] = geocoding_progress(session)
        aggregate["map_dirty_marked"] = _mark_map_dirty() if aggregate["geocoded"] else False
        aggregate["status"] = "completed"
        _campaign_state(session_factory, task_id, status="completed", progress=aggregate, result=aggregate)
        return aggregate
    except Exception as exc:
        aggregate["status"] = "failed"
        aggregate["phase"] = "failed"
        aggregate["error"] = str(exc)[:2000]
        aggregate["elapsed_seconds"] = round(time.monotonic() - started, 1)
        _campaign_state(
            session_factory,
            task_id,
            status="failed",
            progress=aggregate,
            result=aggregate,
            error=exc.__class__.__name__ + ": " + str(exc)[:1800],
        )
        raise


def latest_geo_fast_drain(session: Any) -> dict[str, Any] | None:
    row = session.scalar(
        select(BackgroundTaskState)
        .where(BackgroundTaskState.task_type == P7_TASK_TYPE)
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "task_id": row.task_id,
        "status": row.status,
        "progress": row.progress_json,
        "result": row.result_json,
        "error": row.error_message,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }
