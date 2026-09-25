from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time
from typing import Any

from redis import Redis
from redis.exceptions import LockError
from sqlalchemy import case, delete, exists, func, or_, select

from bankrotai.core import get_settings, utc_now
from bankrotai.db import AppSetting, BackgroundTaskState, GeoFailure, GeoQueryCache, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import (
    CadastralObjectResult,
    IK12_GEOCODER,
    apply_lot_geo_result,
    build_geocoding_address_candidates,
    resolve_lot_geo,
    validate_geocoding_result,
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
_SAVE_CHUNK_SIZE = 100
_CAMPAIGN_TASK_ID = re.compile(r"^(geo-\d{8}-\d{6})-")
_GEO_STRATEGY_SETTING_KEY = "geocoding_strategy_version"
_GEO_STRATEGY_VERSION = "2026-09-25-photon-structured-cfo-v1"
_CFO_REGION_CODES = frozenset({
    "31", "32", "33", "36", "37", "40", "44", "46", "48",
    "50", "57", "62", "67", "68", "69", "71", "76", "77",
})


def _elapsed_seconds_since(value: datetime) -> int:
    """Treat persisted naive task timestamps as UTC on every SQL dialect."""
    started_at = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return max(0, round((utc_now() - started_at).total_seconds()))


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


def _refresh_failures_for_current_strategy(session: Any) -> int:
    """Allow a materially improved resolver to retry old failures immediately once.

    Retry backoff reaches seven days after repeated misses. Without a strategy
    marker, newly deployed provider/candidate improvements would not reach much
    of the backlog until the old timer elapsed. The marker makes the reset
    idempotent across deploys and workers.
    """

    marker = session.scalar(
        select(AppSetting).where(AppSetting.key == _GEO_STRATEGY_SETTING_KEY)
    )
    if marker is not None and marker.value == _GEO_STRATEGY_VERSION:
        return 0

    retryable_lot_ids = select(ProcessedLot.id).where(
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        or_(
            ProcessedLot.cadastral_number.is_not(None),
            ProcessedLot.address.is_not(None),
        ),
        ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
    )

    failures = session.scalars(
        select(GeoFailure).where(GeoFailure.lot_id.in_(retryable_lot_ids))
    ).all()
    requeued = 0
    now = utc_now()
    for failure in failures:
        message = str(failure.error_message or "")
        # Strategy refresh is for provider/candidate misses and validation
        # changes, not arbitrary operational exceptions.
        if (
            "no_coordinates" not in message
            and "mismatch" not in message
            and "No validated" not in message
            and "Geocoding chain returned no result" not in message
        ):
            continue
        failure.status = "queued"
        failure.attempt_count = 0
        failure.next_retry_at = now
        requeued += 1

    if marker is None:
        marker = AppSetting(
            key=_GEO_STRATEGY_SETTING_KEY,
            value=_GEO_STRATEGY_VERSION,
        )
        session.add(marker)
    else:
        marker.value = _GEO_STRATEGY_VERSION
    session.commit()
    return requeued


@dataclass(frozen=True, slots=True)
class GeoWorkItem:
    lot_id: int
    cadastral_number: str | None
    cadastral_numbers: list[str] | None
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
        "cads": sorted({
            "".join(str(value).casefold().split())
            for value in (item.cadastral_numbers or [])
            if value
        }),
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
    task_type: str = "geocoding",
) -> None:
    if not task_id:
        return
    with session_factory() as session:
        state = session.scalar(select(BackgroundTaskState).where(BackgroundTaskState.task_id == task_id))
        if state is None:
            state = BackgroundTaskState(task_id=task_id, task_type=task_type, status=status)
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
        "cadastral_numbers": sorted({
            "".join(str(value).casefold().split())
            for value in (lot.cadastral_numbers or [])
            if value
        }),
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
    ranked = (
        select(
            LotGeoSnapshot.id.label("geo_id"),
            LotGeoSnapshot.lot_id,
            func.row_number()
            .over(
                partition_by=LotGeoSnapshot.lot_id,
                order_by=(LotGeoSnapshot.observed_at.desc(), LotGeoSnapshot.id.desc()),
            )
            .label("position"),
        )
        .join(ProcessedLot, ProcessedLot.id == LotGeoSnapshot.lot_id)
        .where(ProcessedLot.is_archived.is_(False))
        .subquery()
    )
    latest_ids = select(ranked.c.lot_id, ranked.c.geo_id).where(ranked.c.position == 1).subquery()
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


def geocoding_quality_audit(session: Any, *, hotspot_min_lots: int = 5) -> dict[str, Any]:
    """Read-only diagnostics for suspicious latest coordinates and address matches."""
    from collections import defaultdict

    from bankrotai.geo import expected_locality_name

    ranked = select(
        LotGeoSnapshot.id.label("geo_id"),
        func.row_number()
        .over(
            partition_by=LotGeoSnapshot.lot_id,
            order_by=(LotGeoSnapshot.observed_at.desc(), LotGeoSnapshot.id.desc()),
        )
        .label("position"),
    ).subquery()
    rows = session.execute(
        select(ProcessedLot.id, ProcessedLot.address, LotGeoSnapshot)
        .join(LotGeoSnapshot, LotGeoSnapshot.lot_id == ProcessedLot.id)
        .join(ranked, ranked.c.geo_id == LotGeoSnapshot.id)
        .where(
            ranked.c.position == 1,
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
        )
    ).all()
    hotspots: dict[tuple[float, float], list[int]] = defaultdict(list)
    invalid_ids: list[int] = []
    locality_mismatch_ids: list[int] = []
    for lot_id, address, snapshot in rows:
        lat, lon = float(snapshot.centroid_lat), float(snapshot.centroid_lon)
        if not (41.0 <= lat <= 82.0 and 19.0 <= lon <= 180.0):
            invalid_ids.append(lot_id)
        hotspots[(round(lat, 4), round(lon, 4))].append(lot_id)
        expected = expected_locality_name(address)
        observed = str((snapshot.metadata_json or {}).get("address") or "").casefold()
        if expected and observed and expected not in observed:
            locality_mismatch_ids.append(lot_id)
    hotspot_rows: list[dict[str, Any]] = [
        {"lat": key[0], "lon": key[1], "lot_count": len(ids), "sample_lot_ids": ids[:10]}
        for key, ids in hotspots.items()
        if len(ids) >= hotspot_min_lots
    ]
    hotspot_rows.sort(key=lambda item: int(item["lot_count"]), reverse=True)
    return {
        "audited_lots": len(rows),
        "invalid_coordinate_count": len(invalid_ids),
        "invalid_coordinate_sample_lot_ids": invalid_ids[:50],
        "locality_mismatch_count": len(locality_mismatch_ids),
        "locality_mismatch_sample_lot_ids": locality_mismatch_ids[:50],
        "coordinate_hotspot_count": len(hotspot_rows),
        "coordinate_hotspots": hotspot_rows[:50],
    }


_CFO_REGION_CODES = frozenset({
    "31", "32", "33", "36", "37", "40", "44", "46", "48",
    "50", "57", "62", "67", "68", "69", "71", "76", "77",
})


def _failure_reason_from_message(message: str) -> str:
    """Aggregate a persisted failure without exposing its query/address."""
    try:
        payload = json.loads(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        value = str(message or "").strip()
        if not value:
            return "unknown"
        # Provider/network exceptions are useful operationally, while arbitrary
        # text can contain addresses. Keep only a conservative class prefix.
        if ":" in value:
            prefix = value.split(":", 1)[0].strip()
            if prefix and len(prefix) <= 80 and " " not in prefix:
                return prefix[:80]
        return "unclassified"

    attempts = payload.get("attempts") if isinstance(payload, dict) else None
    if isinstance(attempts, list):
        for attempt in reversed(attempts):
            if not isinstance(attempt, dict):
                continue
            reason = attempt.get("reason")
            source = attempt.get("source")
            if reason:
                return f"{source or 'provider'}:{reason}"[:160]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, str) and error:
        return error.split(":", 1)[0][:80]
    return "no_validated_coordinates"


def geocoding_diagnostic_report(session: Any) -> dict[str, Any]:
    """Aggregate production geocoding quality without returning raw addresses."""
    population = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        or_(ProcessedLot.cadastral_number.isnot(None), ProcessedLot.address.isnot(None)),
    )
    has_geo = exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id)

    coverage_rows = session.execute(
        select(
            ProcessedLot.region_code,
            func.count().label("eligible"),
            func.sum(case((has_geo, 1), else_=0)).label("mapped"),
        )
        .where(*population, ProcessedLot.region_code.in_(sorted(_CFO_REGION_CODES)))
        .group_by(ProcessedLot.region_code)
    ).all()
    cfo = {}
    for region_code, eligible, mapped in coverage_rows:
        eligible_count = int(eligible or 0)
        mapped_count = int(mapped or 0)
        cfo[str(region_code)] = {
            "eligible": eligible_count,
            "mapped": mapped_count,
            "unmapped": max(0, eligible_count - mapped_count),
            "percent": round(mapped_count / eligible_count * 100, 1) if eligible_count else 100.0,
        }

    failure_rows = session.execute(
        select(GeoFailure.status, GeoFailure.attempt_count, GeoFailure.error_message)
        .join(ProcessedLot, ProcessedLot.id == GeoFailure.lot_id)
        .where(*population, GeoFailure.status != "resolved")
    ).all()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    attempt_counts: Counter[str] = Counter()
    for status, attempts, message in failure_rows:
        status_counts[str(status or "unknown")] += 1
        reason_counts[_failure_reason_from_message(str(message or ""))] += 1
        attempt_counts[str(int(attempts or 0))] += 1

    candidate_rows = session.execute(
        select(ProcessedLot.region_code, ProcessedLot.cadastral_number)
        .where(
            *population,
            ProcessedLot.region_code.isnot(None),
            ProcessedLot.cadastral_number.isnot(None),
        )
    ).all()
    cadastral_region_comparable = 0
    cadastral_region_mismatch = 0
    mismatch_pairs: Counter[str] = Counter()
    for region_code, cadastral_number in candidate_rows:
        prefix = str(cadastral_number or "").strip().split(":", 1)[0]
        if not (prefix.isdigit() and 1 <= len(prefix) <= 2):
            continue
        prefix = prefix.zfill(2)
        # Only compare codes where cadastral and application subject codes use
        # the same canonical two-digit identifier. This intentionally skips
        # special cadastral districts not present in our canonical directory.
        if prefix not in _CFO_REGION_CODES:
            continue
        canonical = str(region_code or "").strip().zfill(2)
        if not canonical.isdigit():
            continue
        cadastral_region_comparable += 1
        if canonical != prefix:
            cadastral_region_mismatch += 1
            mismatch_pairs[f"{canonical}->{prefix}"] += 1

    audit = geocoding_quality_audit(session)
    progress = geocoding_progress(session)
    statistics = geocoding_statistics(session)

    recent_batches = session.scalars(
        select(BackgroundTaskState)
        .where(
            BackgroundTaskState.task_type == "geocoding",
            BackgroundTaskState.status == "completed",
        )
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(20)
    ).all()
    recent_provider_counts: Counter[str] = Counter()
    recent_failure_reasons: Counter[str] = Counter()
    for batch in recent_batches:
        result = batch.result_json or {}
        for key, value in (result.get("provider_counts") or {}).items():
            recent_provider_counts[str(key)] += int(value or 0)
        for key, value in (result.get("failure_reasons") or {}).items():
            recent_failure_reasons[str(key)] += int(value or 0)

    ik12_recovery_rows = session.scalars(
        select(BackgroundTaskState)
        .where(
            BackgroundTaskState.task_type == "geocoding_ik12_recovery",
            BackgroundTaskState.status == "completed",
        )
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(20)
    ).all()
    ik12_processed = 0
    ik12_recovered = 0
    ik12_failed = 0
    ik12_duration = 0.0
    ik12_failure_reasons: Counter[str] = Counter()
    for batch in ik12_recovery_rows:
        value = batch.result_json or {}
        ik12_processed += int(value.get("processed") or 0)
        ik12_recovered += int(value.get("recovered") or 0)
        ik12_failed += int(value.get("failed") or 0)
        ik12_duration += float(value.get("duration_seconds") or 0.0)
        for key, count in (value.get("failure_reasons") or {}).items():
            ik12_failure_reasons[str(key)] += int(count or 0)

    return {
        "progress": progress,
        "statistics": statistics,
        "cfo": cfo,
        "failures": {
            "total_open": len(failure_rows),
            "by_status": dict(status_counts.most_common()),
            "by_attempt_count": dict(sorted(attempt_counts.items(), key=lambda item: int(item[0]))),
            "top_reasons": dict(reason_counts.most_common(25)),
        },
        "recent_batches": {
            "count": len(recent_batches),
            "provider_counts": dict(recent_provider_counts.most_common()),
            "failure_reasons": dict(recent_failure_reasons.most_common(25)),
        },
        "ik12_recovery": {
            "batch_count": len(ik12_recovery_rows),
            "processed": ik12_processed,
            "recovered": ik12_recovered,
            "failed": ik12_failed,
            "hit_rate_percent": round(ik12_recovered / ik12_processed * 100, 1) if ik12_processed else None,
            "total_duration_seconds": round(ik12_duration, 2),
            "average_seconds": round(ik12_duration / ik12_processed, 3) if ik12_processed else None,
            "failure_reasons": dict(ik12_failure_reasons.most_common(15)),
        },
        "quality": {
            "audited_lots": int(audit["audited_lots"]),
            "invalid_coordinate_count": int(audit["invalid_coordinate_count"]),
            "locality_mismatch_count": int(audit["locality_mismatch_count"]),
            "coordinate_hotspot_count": int(audit["coordinate_hotspot_count"]),
            "largest_coordinate_hotspots": [
                {
                    "lat": item["lat"],
                    "lon": item["lon"],
                    "lot_count": item["lot_count"],
                }
                for item in audit["coordinate_hotspots"][:20]
            ],
            "cadastral_region_comparable": cadastral_region_comparable,
            "cadastral_region_mismatch": cadastral_region_mismatch,
            "top_cadastral_region_mismatches": dict(mismatch_pairs.most_common(20)),
        },
    }


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
        (
            int((row.result_json or {}).get("processed") or 0),
            float((row.result_json or {}).get("duration_seconds") or 0),
        )
        for row in recent
    ]
    sample_lots = sum(processed for processed, seconds in samples if processed > 0 and seconds > 0)
    sample_seconds = sum(seconds for processed, seconds in samples if processed > 0 and seconds > 0)
    rate = sample_lots / sample_seconds if sample_lots and sample_seconds else None
    actionable_remaining = max(0, total - geocoded - terminal)
    eta_seconds = math.ceil(actionable_remaining / rate) if rate else None
    elapsed_seconds = None
    if latest is not None and latest.started_at is not None:
        campaign = _CAMPAIGN_TASK_ID.match(latest.task_id)
        if campaign:
            campaign_rows = session.scalars(
                select(BackgroundTaskState).where(
                    BackgroundTaskState.task_type == "geocoding",
                    BackgroundTaskState.task_id.like(f"{campaign.group(1)}-%"),
                )
            ).all()
            completed_seconds = sum(
                float((row.result_json or {}).get("duration_seconds") or 0)
                for row in campaign_rows
                if row.status == "completed"
            )
            active_seconds = _elapsed_seconds_since(latest.started_at) if latest.status == "running" else 0
            elapsed_seconds = math.ceil(completed_seconds + active_seconds)
        elif latest.status == "running":
            elapsed_seconds = _elapsed_seconds_since(latest.started_at)
        else:
            elapsed_seconds = math.ceil(float((latest.result_json or {}).get("duration_seconds") or 0))
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
        "expected_completion_at": (datetime.now(timezone.utc) + timedelta(seconds=eta_seconds)).isoformat()
        if eta_seconds is not None and not paused
        else None,
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


def _geocoding_failure_reason(value: Any) -> str:
    """Return a bounded aggregate label without leaking an address or query."""
    if isinstance(value, Exception):
        return f"exception:{value.__class__.__name__}"
    attempts = getattr(value, "attempts", None) or []
    for attempt in reversed(attempts):
        reason = attempt.get("reason") if isinstance(attempt, dict) else None
        source = attempt.get("source") if isinstance(attempt, dict) else None
        if reason:
            return f"{source or 'provider'}:{reason}"[:160]
    status = getattr(value, "status", None)
    if status:
        return str(status)[:160]
    return "no_validated_coordinates"


def _save_geo_item(session: Any, lot: ProcessedLot, value: Any) -> tuple[bool, str]:
    lot_id = lot.id
    current_input_hash = geo_input_hash(lot)
    if isinstance(value, Exception):
        _record_scheduled_failure(session, lot_id, str(value))
        return False, _geocoding_failure_reason(value)
    if apply_lot_geo_result(session, lot, value):
        lot.geo_input_hash = current_input_hash
        resolve_geo_failure(session, lot_id)
        return True, (value.source or "unknown")
    lot.geo_input_hash = current_input_hash
    _record_scheduled_failure(session, lot_id, _geocoding_failure_message(value))
    return False, _geocoding_failure_reason(value)


def _save_geo_chunk(
    session_factory: Callable[[], Any],
    chunk: list[tuple[str, GeoWorkItem]],
    resolved: dict[str, Any],
) -> list[tuple[bool, str]]:
    """Persist a chunk in one transaction; callers may retry item-by-item on failure."""
    with session_factory() as session:
        lots = {
            lot.id: lot
            for lot in session.scalars(
                select(ProcessedLot).where(ProcessedLot.id.in_([item.lot_id for _, item in chunk]))
            ).all()
        }
        outcomes = [
            _save_geo_item(session, lot, resolved.get(key))
            for key, item in chunk
            if (lot := lots.get(item.lot_id)) is not None
        ]
        session.commit()
        return outcomes


def _save_geo_item_isolated(
    session_factory: Callable[[], Any],
    key: str,
    item: GeoWorkItem,
    resolved: dict[str, Any],
) -> tuple[bool, str] | None:
    """Preserve per-lot fault isolation when the fast chunk transaction fails."""
    try:
        with session_factory() as session:
            lot = session.get(ProcessedLot, item.lot_id)
            if lot is None:
                return None
            outcome = _save_geo_item(session, lot, resolved.get(key))
            session.commit()
            return outcome
    except Exception as exc:
        with session_factory() as session:
            _record_scheduled_failure(session, item.lot_id, str(exc))
            session.commit()
        return False, f"persistence:{exc.__class__.__name__}"[:160]


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
                "status": "paused",
                "paused": True,
                "phase": "paused",
                "queued": 0,
                "processed": 0,
                "geocoded": 0,
                "failed": 0,
                "percent": 0.0,
            }
            _set_progress_state(
                session_factory,
                progress_task_id,
                status="paused",
                progress=paused_result,
                result=paused_result,
            )
            return paused_result
    batch_limit = max(1, min(limit, 1000))
    now = utc_now()
    with session_factory() as session:
        strategy_requeued = _refresh_failures_for_current_strategy(session)
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
                ProcessedLot.cadastral_numbers,
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
                case(
                    (ProcessedLot.region_code.in_(_CFO_REGION_CODES), 0),
                    else_=1,
                ),
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
        "strategy_version": _GEO_STRATEGY_VERSION,
        "strategy_requeued": strategy_requeued,
        "queued": len(items),
        "processed": 0,
        "geocoded": 0,
        "failed": 0,
        "unique_queries": len(groups),
        "deduplicated": len(items) - len(groups),
        "cache_hits": 0,
        "resolved_queries": 0,
        "provider_counts": {},
        "failure_reasons": {},
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
                cadastral_numbers=values[0].cadastral_numbers,
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
    save_items = [(key, item) for key, group in groups.items() for item in group]
    for chunk_start in range(0, len(save_items), _SAVE_CHUNK_SIZE):
        chunk = save_items[chunk_start : chunk_start + _SAVE_CHUNK_SIZE]
        try:
            outcomes = _save_geo_chunk(session_factory, chunk, resolved)
        except Exception:
            outcomes = [
                outcome
                for key, item in chunk
                if (outcome := _save_geo_item_isolated(session_factory, key, item, resolved)) is not None
            ]

        geocoded = 0
        provider_counts: dict[str, int] = {}
        failure_reasons: dict[str, int] = {}
        for success, label in outcomes:
            if success:
                geocoded += 1
                provider_counts[label] = provider_counts.get(label, 0) + 1
            else:
                failure_reasons[label] = failure_reasons.get(label, 0) + 1
        failed = len(outcomes) - geocoded
        result["geocoded"] += geocoded
        result["failed"] += failed
        result["processed"] += geocoded + failed
        for provider, count in provider_counts.items():
            result["provider_counts"][provider] = result["provider_counts"].get(provider, 0) + count
        for reason, count in failure_reasons.items():
            result["failure_reasons"][reason] = result["failure_reasons"].get(reason, 0) + count
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


def _has_nspd_no_coordinate_attempt(message: str | None) -> bool:
    """Identify a normal-chain NSPD miss without depending on the final provider."""
    try:
        payload = json.loads(str(message or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    attempts = payload.get("attempts") if isinstance(payload, dict) else None
    if not isinstance(attempts, list):
        return False
    return any(
        isinstance(attempt, dict)
        and str(attempt.get("source") or "").startswith("nspd_cadastral")
        and attempt.get("reason") == "no_coordinates"
        for attempt in attempts
    )


def recover_nspd_failures_with_ik12(
    session_factory: Callable[[], Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Sequential, bounded IK12 recovery for repeated NSPD cadastral misses.

    This deliberately does not increment normal GeoFailure attempt counters on
    a recovery miss. Successful results use the normal validation and
    persistence path and resolve the existing failure.
    """
    started_at = time.monotonic()
    batch_limit = max(1, min(int(limit), 25))

    with session_factory() as session:
        if is_geocoding_paused(session):
            return {
                "status": "paused",
                "queued": 0,
                "processed": 0,
                "recovered": 0,
                "failed": 0,
            }
        candidates = session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.cadastral_number,
                ProcessedLot.address,
                ProcessedLot.region_name,
                GeoFailure.error_message,
                GeoFailure.attempt_count,
            )
            .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                ProcessedLot.cadastral_number.is_not(None),
                GeoFailure.status.in_(("queued", "terminal")),
                GeoFailure.attempt_count >= 2,
            )
            .order_by(GeoFailure.attempt_count.desc(), GeoFailure.last_failed_at.desc())
            .limit(max(batch_limit * 20, 100))
        ).all()

    selected = [
        row for row in candidates
        if _has_nspd_no_coordinate_attempt(row.error_message)
    ][:batch_limit]

    result: dict[str, Any] = {
        "status": "completed",
        "queued": len(selected),
        "processed": 0,
        "recovered": 0,
        "failed": 0,
        "failure_reasons": {},
        "duration_seconds": 0.0,
        "average_seconds": 0.0,
    }
    reasons: Counter[str] = Counter()

    for row in selected:
        cadastral_number = re.sub(r"\s+", "", str(row.cadastral_number or ""))
        if not cadastral_number:
            reasons["missing_cadastral_number"] += 1
            result["failed"] += 1
            result["processed"] += 1
            continue

        try:
            candidate = IK12_GEOCODER.search_by_cadastral_number(cadastral_number)
            valid, reason = validate_geocoding_result(
                candidate,
                cadastral_number=cadastral_number,
                address=row.address,
                region_name=row.region_name,
            )
        except Exception as exc:
            candidate = None
            valid = False
            reason = f"exception:{exc.__class__.__name__}"

        if not valid or candidate is None:
            reasons[str(reason)] += 1
            result["failed"] += 1
            result["processed"] += 1
            continue

        with session_factory() as session:
            lot = session.get(ProcessedLot, int(row.id))
            if lot is None:
                reasons["lot_disappeared"] += 1
                result["failed"] += 1
                result["processed"] += 1
                continue
            # Revalidate against the current lot values in case ingestion changed
            # while the external request was in flight.
            valid_now, reason_now = validate_geocoding_result(
                candidate,
                cadastral_number=lot.cadastral_number,
                address=lot.address,
                region_name=lot.region_name,
            )
            if not valid_now or not apply_lot_geo_result(session, lot, candidate):
                reasons[str(reason_now)] += 1
                result["failed"] += 1
                result["processed"] += 1
                session.rollback()
                continue
            lot.geo_input_hash = geo_input_hash(lot)
            resolve_geo_failure(session, lot.id)
            session.commit()

        result["recovered"] += 1
        result["processed"] += 1

    duration = time.monotonic() - started_at
    result["failure_reasons"] = dict(reasons.most_common())
    result["duration_seconds"] = round(duration, 2)
    result["average_seconds"] = round(duration / result["processed"], 3) if result["processed"] else 0.0
    return result


def run_ik12_recovery_batch(
    session_factory: Callable[[], Any],
    *,
    limit: int = 5,
    progress_task_id: str | None = None,
) -> dict[str, Any]:
    """Serialize IK12 recovery with the normal production geocoding batch."""
    with session_factory() as session:
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "sqlite":
        result = recover_nspd_failures_with_ik12(session_factory, limit=limit)
    else:
        try:
            with _distributed_geo_lock():
                result = recover_nspd_failures_with_ik12(session_factory, limit=limit)
        except GeoBatchAlreadyRunning:
            result = {
                "status": "busy",
                "queued": 0,
                "processed": 0,
                "recovered": 0,
                "failed": 0,
            }
    _set_progress_state(
        session_factory,
        progress_task_id,
        status="completed",
        progress=result,
        result=result,
        task_type="geocoding_ik12_recovery",
    )
    return result
