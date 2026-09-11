from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import math
import re
import time
from typing import Any

from redis import Redis
from redis.exceptions import LockError
from sqlalchemy import delete, exists, func, or_, select

from bankrotai.core import get_settings, utc_now
from bankrotai.db import AppSetting, BackgroundTaskState, GeoFailure, GeoQueryCache, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import (
    CadastralObjectResult,
    apply_lot_geo_result,
    build_geocoding_address_candidates,
    resolve_lot_geo,
)
from bankrotai.services.quality import record_geo_failure, resolve_geo_failure


_BASE_RETRY_SECONDS = 21_600
_MAX_RETRY_SECONDS = 604_800
_MAX_ATTEMPTS = 8
_GEO_LOCK_NAME = "bankrotai:geocoding:batch"
_GEO_LOCK_SECONDS = 3600
_SUCCESS_CACHE_DAYS = 30
_GEOCODING_PAUSED_KEY = "geocoding_paused"
_ETA_SAMPLE_BATCHES = 20
_CAMPAIGN_TASK_ID = re.compile(r"^(geo-\d{8}-\d{6})-")


class GeoBatchAlreadyRunning(RuntimeError):
    pass


def is_geocoding_paused(session: Any) -> bool:
    setting = session.scalar(select(AppSetting).where(AppSetting.key == _GEOCODING_PAUSED_KEY))
    return setting is not None and setting.value.lower() in {"1", "true", "yes"}


def set_geocoding_paused(session: Any, paused: bool) -> bool:
    setting = session.scalar(select(AppSetting).where(AppSetting.key == _GEOCODING_PAUSED_KEY))
    if setting is None:
        setting = AppSetting(key=_GEOCODING_PAUSED_KEY, value="true" if paused else "false")
        session.add(setting)
    else:
        setting.value = "true" if paused else "false"
    session.flush()
    return paused


@dataclass(frozen=True, slots=True)
class GeoWorkItem:
    lot_id: int
    cadastral_number: str | None
    address: str | None
    title: str | None
    description: str | None
    region_name: str | None


def _work_key(item: GeoWorkItem) -> str:
    address_candidates = build_geocoding_address_candidates(
        item.address,
        title=item.title,
        description=item.description,
        region_name=item.region_name,
    )
    value = {
        "cad": "".join((item.cadastral_number or "").casefold().split()),
        "address": " ".join((address_candidates[0] if address_candidates else "").casefold().split()),
        "region": " ".join((item.region_name or "").casefold().split()),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cache_payload(value: CadastralObjectResult) -> dict[str, Any]:
    return {
        "query": value.query,
        "cadastral_number": value.cadastral_number,
        "object_type": value.object_type,
        "title": value.title,
        "address": value.address,
        "lat": value.lat,
        "lon": value.lon,
        "geometry_json": value.geometry_json,
        "has_boundary": value.has_boundary,
        "source": value.source,
        "confidence": value.confidence,
        "info": value.info,
        "status": value.status,
        "attempts": value.attempts,
    }


def _cached_result(payload: dict[str, Any]) -> CadastralObjectResult:
    allowed = set(CadastralObjectResult.__dataclass_fields__) - {"raw", "error"}
    return CadastralObjectResult(**{key: value for key, value in payload.items() if key in allowed})


def _set_progress_state(
    session_factory: Callable[[], Any],
    task_id: str | None,
    *,
    status: str,
    progress: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if not task_id:
        return
    with session_factory() as session:
        state = session.scalar(select(BackgroundTaskState).where(BackgroundTaskState.task_id == task_id))
        if state is None:
            state = BackgroundTaskState(task_id=task_id, task_type="geocoding", status=status)
            session.add(state)
        state.status = status
        state.progress_json = progress
        state.result_json = result
        state.error_message = error
        state.started_at = state.started_at or utc_now()
        if status in {"completed", "failed"}:
            state.finished_at = utc_now()
        session.commit()


def _mark_progress_failed(
    session_factory: Callable[[], Any],
    task_id: str | None,
    error: Exception,
) -> None:
    if not task_id:
        return
    with session_factory() as session:
        state = session.scalar(select(BackgroundTaskState).where(BackgroundTaskState.task_id == task_id))
        if state is None:
            state = BackgroundTaskState(task_id=task_id, task_type="geocoding", status="failed")
            session.add(state)
        state.status = "failed"
        state.error_message = str(error)[:2000]
        state.finished_at = utc_now()
        session.commit()


def geo_input_hash(lot: ProcessedLot) -> str:
    payload = {
        "address": " ".join((lot.address or "").casefold().split()),
        "cadastral_number": "".join((lot.cadastral_number or "").casefold().split()),
        "description": " ".join((lot.description or "").casefold().split()),
        "region_name": " ".join((lot.region_name or "").casefold().split()),
        "title": " ".join((lot.title or "").casefold().split()),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@contextmanager
def _distributed_geo_lock():
    client = Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    lock = client.lock(_GEO_LOCK_NAME, timeout=_GEO_LOCK_SECONDS, blocking_timeout=0)
    acquired = False
    try:
        acquired = bool(lock.acquire(blocking=False))
        if not acquired:
            raise GeoBatchAlreadyRunning("Another geocoding batch is already running")
        yield
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                pass
        client.close()


def geocoding_statistics(session: Any) -> dict[str, int]:
    latest_ids = (
        select(LotGeoSnapshot.lot_id, func.max(LotGeoSnapshot.id).label("geo_id"))
        .join(ProcessedLot, ProcessedLot.id == LotGeoSnapshot.lot_id)
        .where(ProcessedLot.is_archived.is_(False))
        .group_by(LotGeoSnapshot.lot_id)
        .subquery()
    )
    rows = session.execute(
        select(LotGeoSnapshot.geo_source, LotGeoSnapshot.geo_confidence, func.count())
        .join(latest_ids, LotGeoSnapshot.id == latest_ids.c.geo_id)
        .group_by(LotGeoSnapshot.geo_source, LotGeoSnapshot.geo_confidence)
    ).all()
    active = int(session.scalar(select(func.count()).where(ProcessedLot.is_archived.is_(False))) or 0)
    with_coordinates = sum(int(count) for _source, _confidence, count in rows)
    result = {
        "active_lots": active,
        "with_coordinates": with_coordinates,
        "without_coordinates": max(0, active - with_coordinates),
        "ik12": 0,
        "nspd": 0,
        "address": 0,
        "low_confidence": 0,
        "geocoding_failed": int(
            session.scalar(select(func.count()).where(GeoFailure.status.not_in(("resolved",)))) or 0
        ),
    }
    for source, confidence, count in rows:
        key = (
            "ik12"
            if source == "ik12_cadastral"
            else "nspd"
            if source == "nspd"
            else "address"
            if source in {"nominatim", "photon"}
            else None
        )
        if key:
            result[key] += int(count)
        if confidence in {"low", "none", "unknown"}:
            result["low_confidence"] += int(count)
    return result


def geocoding_progress(session: Any) -> dict[str, Any]:
    """Return user-facing, exact queue counters plus the latest durable batch progress."""
    population = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        or_(ProcessedLot.cadastral_number.isnot(None), ProcessedLot.address.isnot(None)),
    )
    total = int(session.scalar(select(func.count()).select_from(ProcessedLot).where(*population)) or 0)
    geocoded = int(
        session.scalar(
            select(func.count())
            .select_from(ProcessedLot)
            .where(
                *population,
                exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
            )
        )
        or 0
    )
    terminal = int(
        session.scalar(
            select(func.count())
            .select_from(GeoFailure)
            .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
            .where(
                *population,
                GeoFailure.status == "terminal",
            )
        )
        or 0
    )
    latest = session.scalar(
        select(BackgroundTaskState)
        .where(BackgroundTaskState.task_type == "geocoding")
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(1)
    )
    recent = session.scalars(
        select(BackgroundTaskState)
        .where(
            BackgroundTaskState.task_type == "geocoding",
            BackgroundTaskState.status == "completed",
        )
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(_ETA_SAMPLE_BATCHES)
    ).all()
    samples = [
        (int((row.result_json or {}).get("processed") or 0), float((row.result_json or {}).get("duration_seconds") or 0))
        for row in recent
    ]
    sample_lots = sum(processed for processed, seconds in samples if processed > 0 and seconds > 0)
    sample_seconds = sum(seconds for processed, seconds in samples if processed > 0 and seconds > 0)
    rate = sample_lots / sample_seconds if sample_lots and sample_seconds else None
    actionable_remaining = max(0, total - geocoded - terminal)
    eta_seconds = math.ceil(actionable_remaining / rate) if rate else None
    elapsed_seconds = None
    if latest is not None and latest.started_at is not None:
        operation_started = latest.started_at
        campaign = _CAMPAIGN_TASK_ID.match(latest.task_id)
        if campaign:
            first_started = session.scalar(
                select(func.min(BackgroundTaskState.started_at)).where(
                    BackgroundTaskState.task_type == "geocoding",
                    BackgroundTaskState.task_id.like(f"{campaign.group(1)}-%"),
                )
            )
            operation_started = first_started or operation_started
        elapsed_seconds = max(0, round((utc_now() - operation_started).total_seconds()))
    paused = is_geocoding_paused(session)
    return {
        "total": total,
        "geocoded": geocoded,
        "remaining": max(0, total - geocoded),
        "terminal_failures": terminal,
        "actionable_remaining": actionable_remaining,
        "percent": round((geocoded / total * 100) if total else 100.0, 1),
        "paused": paused,
        "rate_per_second": round(rate, 3) if rate else None,
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed_seconds,
        "estimated_total_seconds": (elapsed_seconds + eta_seconds)
        if elapsed_seconds is not None and eta_seconds is not None
        else None,
        "task": None
        if latest is None
        else {
            "task_id": latest.task_id,
            "status": latest.status,
            "progress": latest.progress_json,
            "result": latest.result_json,
            "error": latest.error_message,
            "started_at": latest.started_at,
            "finished_at": latest.finished_at,
        },
    }


def _record_scheduled_failure(session: Any, lot_id: int, error: str) -> None:
    failure = record_geo_failure(
        session,
        lot_id,
        error,
        retry_after_seconds=_BASE_RETRY_SECONDS,
    )
    if failure.attempt_count >= _MAX_ATTEMPTS:
        failure.status = "terminal"
        failure.next_retry_at = None
        return
    delay = min(
        _BASE_RETRY_SECONDS * (2 ** (failure.attempt_count - 1)),
        _MAX_RETRY_SECONDS,
    )
    failure.next_retry_at = utc_now() + timedelta(seconds=delay)


def _geocoding_failure_message(value: Any) -> str:
    if value is None:
        return "Geocoding chain returned no result"
    payload = {
        "error": getattr(value, "error", None) or "No validated coordinates",
        "attempts": getattr(value, "attempts", None) or [],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:2000]


def _geocode_pending_lots_unlocked(
    session_factory: Callable[[], Any],
    *,
    limit: int = 250,
    re_geocode_existing: bool = False,
    progress_task_id: str | None = None,
) -> dict[str, Any]:
    """Geocode a bounded production batch without holding a DB transaction during the whole run."""
    started_at = time.monotonic()
    with session_factory() as session:
        if is_geocoding_paused(session):
            paused_result = {
                "status": "paused", "paused": True, "phase": "paused", "queued": 0,
                "processed": 0, "geocoded": 0, "failed": 0, "percent": 0.0,
            }
            _set_progress_state(
                session_factory, progress_task_id, status="paused",
                progress=paused_result, result=paused_result,
            )
            return paused_result
    batch_limit = max(1, min(limit, 1000))
    now = utc_now()
    with session_factory() as session:
        latest_geo_id = (
            select(func.max(LotGeoSnapshot.id))
            .where(LotGeoSnapshot.lot_id == ProcessedLot.id)
            .correlate(ProcessedLot)
            .scalar_subquery()
        )
        pending_filter = (
            or_(
                ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
                ProcessedLot.needs_geo_check.is_(True),
                exists().where(
                    (LotGeoSnapshot.id == latest_geo_id)
                    & or_(
                        LotGeoSnapshot.geo_confidence.in_(("low", "none", "unknown")),
                        LotGeoSnapshot.geo_source.not_in(("ik12_cadastral", "nspd", "nominatim", "photon")),
                    )
                ),
            )
            if re_geocode_existing
            else or_(
                ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
                (ProcessedLot.needs_geo_check.is_(True) & ProcessedLot.geo_input_hash.is_(None)),
            )
        )
        rows = session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.cadastral_number,
                ProcessedLot.address,
                ProcessedLot.title,
                ProcessedLot.description,
                ProcessedLot.region_name,
            )
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                or_(
                    ProcessedLot.cadastral_number.isnot(None),
                    ProcessedLot.address.isnot(None),
                ),
                pending_filter,
                or_(
                    GeoFailure.id.is_(None),
                    GeoFailure.next_retry_at.is_(None),
                    GeoFailure.next_retry_at <= now,
                ),
                or_(GeoFailure.status.is_(None), GeoFailure.status != "terminal"),
            )
            .order_by(
                GeoFailure.id.is_(None).desc(),
                ProcessedLot.needs_geo_check.desc(),
                ProcessedLot.last_update.desc(),
            )
            .limit(batch_limit)
        ).all()
        items = [GeoWorkItem(*row) for row in rows]

    groups: dict[str, list[GeoWorkItem]] = {}
    for item in items:
        groups.setdefault(_work_key(item), []).append(item)
    result: dict[str, Any] = {
        "queued": len(items),
        "processed": 0,
        "geocoded": 0,
        "failed": 0,
        "unique_queries": len(groups),
        "deduplicated": len(items) - len(groups),
        "cache_hits": 0,
        "resolved_queries": 0,
        "provider_counts": {},
        "phase": "resolving",
    }
    _set_progress_state(session_factory, progress_task_id, status="running", progress={**result, "percent": 0.0})

    resolved: dict[str, Any] = {}
    if groups:
        with session_factory() as session:
            session.execute(delete(GeoQueryCache).where(GeoQueryCache.expires_at <= utc_now()))
            cached_rows = session.scalars(
                select(GeoQueryCache).where(
                    GeoQueryCache.cache_key.in_(list(groups)),
                    GeoQueryCache.expires_at > utc_now(),
                )
            ).all()
            for cached in cached_rows:
                resolved[cached.cache_key] = _cached_result(cached.result_json)
                cached.hit_count += len(groups[cached.cache_key])
                result["cache_hits"] += len(groups[cached.cache_key])
                result["resolved_queries"] += 1
            session.commit()
    missing_groups = {key: values for key, values in groups.items() if key not in resolved}
    workers = min(get_settings().geo_max_workers, max(1, len(missing_groups)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geo-bulk") as executor:
        futures = {
            executor.submit(
                resolve_lot_geo,
                values[0].cadastral_number,
                values[0].address,
                title=values[0].title,
                description=values[0].description,
                region_name=values[0].region_name,
                bulk=True,
            ): key
            for key, values in missing_groups.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                resolved[key] = future.result()
            except Exception as exc:
                resolved[key] = exc
            result["resolved_queries"] += 1
            if result["resolved_queries"] == result["unique_queries"] or result["resolved_queries"] % 5 == 0:
                _set_progress_state(
                    session_factory,
                    progress_task_id,
                    status="running",
                    progress={
                        **result,
                        "percent": round(result["resolved_queries"] / result["unique_queries"] * 80, 1)
                        if result["unique_queries"]
                        else 80.0,
                    },
                )

    with session_factory() as session:
        for key in missing_groups:
            value = resolved.get(key)
            if not isinstance(value, CadastralObjectResult) or value.lat is None or value.lon is None:
                continue
            cached = session.get(GeoQueryCache, key)
            if cached is None:
                cached = GeoQueryCache(cache_key=key, provider=value.source, result_json={})
                session.add(cached)
            cached.provider = value.source
            cached.result_json = _cache_payload(value)
            cached.expires_at = utc_now() + timedelta(days=_SUCCESS_CACHE_DAYS)
        session.commit()

    result["phase"] = "saving"
    for key, group in groups.items():
        for item in group:
            lot_id = item.lot_id
            try:
                value = resolved[key]
                if isinstance(value, Exception):
                    raise value
                with session_factory() as session:
                    lot = session.get(ProcessedLot, lot_id)
                    if lot is None:
                        continue
                    current_input_hash = geo_input_hash(lot)
                    if apply_lot_geo_result(session, lot, value):
                        lot.geo_input_hash = current_input_hash
                        resolve_geo_failure(session, lot_id)
                        result["geocoded"] += 1
                        provider = value.source or "unknown"
                        result["provider_counts"][provider] = result["provider_counts"].get(provider, 0) + 1
                    else:
                        lot.geo_input_hash = current_input_hash
                        _record_scheduled_failure(
                            session,
                            lot_id,
                            _geocoding_failure_message(value),
                        )
                        result["failed"] += 1
                    session.commit()
            except Exception as exc:
                with session_factory() as session:
                    _record_scheduled_failure(session, lot_id, str(exc))
                    session.commit()
                result["failed"] += 1
            result["processed"] += 1
            if result["processed"] == result["queued"] or result["processed"] % 10 == 0:
                percent = round(80 + result["processed"] / result["queued"] * 20, 1) if result["queued"] else 100.0
                _set_progress_state(
                    session_factory,
                    progress_task_id,
                    status="running",
                    progress={**result, "percent": percent},
                )
    result["percent"] = 100.0
    result["phase"] = "completed"
    result["duration_seconds"] = round(time.monotonic() - started_at, 1)
    result["lots_per_second"] = round(result["processed"] / max(result["duration_seconds"], 0.001), 3)
    _set_progress_state(
        session_factory,
        progress_task_id,
        status="completed",
        progress=result,
        result=result,
    )
    return result


def geocode_pending_lots(
    session_factory: Callable[[], Any],
    *,
    limit: int = 250,
    re_geocode_existing: bool = False,
    progress_task_id: str | None = None,
) -> dict[str, Any]:
    """Run one serialized batch; SQLite unit tests do not require the production Redis lock."""
    with session_factory() as session:
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
    try:
        if dialect_name == "sqlite":
            return _geocode_pending_lots_unlocked(
                session_factory,
                limit=limit,
                re_geocode_existing=re_geocode_existing,
                progress_task_id=progress_task_id,
            )
        with _distributed_geo_lock():
            return _geocode_pending_lots_unlocked(
                session_factory,
                limit=limit,
                re_geocode_existing=re_geocode_existing,
                progress_task_id=progress_task_id,
            )
    except Exception as exc:
        _mark_progress_failed(session_factory, progress_task_id, exc)
        raise
