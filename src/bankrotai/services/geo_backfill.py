from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
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
from sqlalchemy import and_, case, delete, exists, func, or_, select

from bankrotai.core import get_settings, utc_now
from bankrotai.db import AppSetting, BackgroundTaskState, GeoFailure, GeoQueryCache, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import (
    CadastralObjectResult,
    IK12_GEOCODER,
    apply_lot_geo_result,
    build_geocoding_address_candidates,
    geocoding_result_quality_score,
    resolve_lot_geo,
    validate_geocoding_result,
)
from bankrotai.services.quality import record_geo_failure, resolve_geo_failure
from bankrotai.services.geo_resilience import (
    GeoProviderUnavailable,
    is_operational_category,
    network_health_snapshot,
    retry_delay_seconds,
)


_BASE_RETRY_SECONDS = 21_600
_MAX_RETRY_SECONDS = 604_800
_MAX_ATTEMPTS = 8
_DEFERRED_STATUSES = ("deferred_no_match", "deferred_validation")
_GEO_LOCK_NAME = "bankrotai:geocoding:batch"
_GEO_LOCK_SECONDS = 3600
_SUCCESS_CACHE_DAYS = 30
_GEOCODING_PAUSED_KEY = "geocoding_paused"
_ETA_SAMPLE_BATCHES = 20
_SAVE_CHUNK_SIZE = 100
_CAMPAIGN_TASK_ID = re.compile(r"^(geo-\d{8}-\d{6})-")
_GEO_STRATEGY_VERSION = "2026-09-25-structured-photon-cfo-v1"
_GEO_STRATEGY_SETTING_PREFIX = "geocoding_strategy_applied:"
_GEO_STRATEGY_SETTING_KEY = f"{_GEO_STRATEGY_SETTING_PREFIX}{_GEO_STRATEGY_VERSION}"
_CFO_REGION_CODES = frozenset({
    "31", "32", "33", "36", "37", "40", "44", "46", "48",
    "50", "57", "62", "67", "68", "69", "71", "76", "77",
})
_RETRYABLE_STRATEGY_ERRORS = frozenset({
    "Geocoding chain returned no result",
    "No validated coordinates",
    "No validated geocoding result",
})

_P9_CANARY_VERSION = "2026-10-05-address-cadastral-quality-v1"
_P9_CANARY_SETTING_KEY = f"geocoding_quality_canary:{_P9_CANARY_VERSION}"
_P9_ADDRESS_SIGNAL = re.compile(
    r"\b(?:корп\.|корпус|стр\.|строение|вл\.|владение|лит\.|литера|"
    r"пом\.|помещение|кв\.|квартира|комната|офис|пр-т|пер\.)\b",
    re.IGNORECASE,
)


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


def _is_strategy_retryable_failure(error_message: str | None) -> bool:
    """Recognize persisted resolver misses without retrying operational errors."""
    message = str(error_message or "").strip()
    try:
        payload = json.loads(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Before structured attempt history, these exact final messages were
        # the resolver's persisted miss semantics. Do not substring-match
        # arbitrary exception text such as connection or storage failures.
        return message in _RETRYABLE_STRATEGY_ERRORS

    if not isinstance(payload, dict):
        return False
    attempts = payload.get("attempts")
    usable_attempts = (
        [
            attempt
            for attempt in attempts
            if isinstance(attempt, dict)
            and any(attempt.get(field) is not None for field in ("source", "reason", "valid"))
        ]
        if isinstance(attempts, list)
        else []
    )
    if usable_attempts:
        return any(
            attempt.get("valid") is False
            and (
                str(attempt.get("reason") or "") == "no_coordinates"
                or str(attempt.get("reason") or "").endswith("_mismatch")
            )
            for attempt in usable_attempts
        )
    return str(payload.get("error") or "") in _RETRYABLE_STRATEGY_ERRORS


def _refresh_failures_for_current_strategy(session: Any) -> int:
    """Requeue eligible historical resolver misses once for a new strategy."""
    marker = session.scalar(
        select(AppSetting).where(AppSetting.key == _GEO_STRATEGY_SETTING_KEY)
    )
    if marker is not None:
        return 0

    eligible_lot_ids = select(ProcessedLot.id).where(
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.auction_status.in_(("active", "scheduled")),
        or_(
            ProcessedLot.cadastral_number.isnot(None),
            ProcessedLot.address.isnot(None),
        ),
        ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
    )
    failures = session.scalars(
        select(GeoFailure).where(
            GeoFailure.lot_id.in_(eligible_lot_ids),
            GeoFailure.status.not_in(("resolved", "p7_queued")),
        )
    ).all()
    now = utc_now()
    requeued = 0
    for failure in failures:
        if not _is_strategy_retryable_failure(failure.error_message):
            continue
        # Preserve the evidence and original failure timestamp; only reset
        # scheduling state so the refreshed resolver gets one new attempt.
        failure.status = "queued"
        failure.attempt_count = 0
        failure.next_retry_at = now
        requeued += 1

    if marker is None:
        session.add(
            AppSetting(
                key=_GEO_STRATEGY_SETTING_KEY,
                value="applied",
            )
        )
    session.commit()
    return requeued


def _p9_canary_seen_ids(session: Any) -> set[int]:
    marker = session.scalar(select(AppSetting).where(AppSetting.key == _P9_CANARY_SETTING_KEY))
    if marker is None or not marker.value:
        return set()
    try:
        payload = json.loads(marker.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    ids = payload.get("lot_ids") if isinstance(payload, dict) else None
    return {int(value) for value in ids or [] if str(value).isdigit()}


def _p9_quality_signal(
    *,
    cadastral_number: str | None,
    address: str | None,
    title: str | None,
    description: str | None,
    error_message: str | None,
) -> str | None:
    text = " ".join(part for part in (address, title, description) if part)
    if _P9_ADDRESS_SIGNAL.search(text):
        return "russian_address_structure"
    if cadastral_number and _has_nspd_no_coordinate_attempt(error_message):
        return "cadastral_provider_address"
    return None


def geo_quality_canary_plan(
    session: Any,
    *,
    limit: int = 200,
    cfo_only: bool = True,
) -> dict[str, Any]:
    """Plan a bounded P9 retry sample without releasing the deferred backlog."""
    batch_limit = max(1, min(int(limit), 500))
    conditions = [
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.auction_status.in_(("active", "scheduled")),
        ProcessedLot.current_geo_lat.is_(None),
        ProcessedLot.current_geo_lon.is_(None),
        GeoFailure.status.in_(_DEFERRED_STATUSES),
    ]
    if cfo_only:
        conditions.append(ProcessedLot.region_code.in_(_CFO_REGION_CODES))

    rows = session.execute(
        select(
            ProcessedLot.id,
            ProcessedLot.region_code,
            ProcessedLot.cadastral_number,
            ProcessedLot.address,
            ProcessedLot.title,
            ProcessedLot.description,
            GeoFailure.status,
            GeoFailure.error_message,
            GeoFailure.last_failed_at,
        )
        .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(*conditions)
        .order_by(
            case((ProcessedLot.region_code.in_(_CFO_REGION_CODES), 0), else_=1),
            GeoFailure.last_failed_at.asc(),
            ProcessedLot.id.asc(),
        )
        .limit(max(batch_limit * 20, 1000))
    ).all()
    already_seen = _p9_canary_seen_ids(session)
    selected: list[dict[str, Any]] = []
    by_signal: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    for row in rows:
        lot_id = int(row.id)
        if lot_id in already_seen:
            continue
        signal = _p9_quality_signal(
            cadastral_number=row.cadastral_number,
            address=row.address,
            title=row.title,
            description=row.description,
            error_message=row.error_message,
        )
        if signal is None:
            continue
        selected.append(
            {
                "lot_id": lot_id,
                "region_code": str(row.region_code or ""),
                "status": str(row.status),
                "signal": signal,
            }
        )
        by_signal[signal] += 1
        by_status[str(row.status)] += 1
        by_region[str(row.region_code or "unknown")] += 1
        if len(selected) >= batch_limit:
            break

    return {
        "version": _P9_CANARY_VERSION,
        "limit": batch_limit,
        "cfo_only": bool(cfo_only),
        "eligible": len(selected),
        "already_requeued": len(already_seen),
        "by_signal": dict(by_signal),
        "by_status": dict(by_status),
        "by_region": dict(by_region.most_common()),
        "sample_lot_ids": [item["lot_id"] for item in selected[:25]],
        "lot_ids": [item["lot_id"] for item in selected],
    }


def requeue_geo_quality_canary(
    session: Any,
    *,
    limit: int = 200,
    cfo_only: bool = True,
) -> dict[str, Any]:
    """Release only the bounded P9 canary selected by geo_quality_canary_plan."""
    plan = geo_quality_canary_plan(session, limit=limit, cfo_only=cfo_only)
    ids = [int(value) for value in plan.pop("lot_ids")]
    if not ids:
        return {**plan, "requeued": 0}

    now = utc_now()
    failures = session.scalars(
        select(GeoFailure).where(
            GeoFailure.lot_id.in_(ids),
            GeoFailure.status.in_(_DEFERRED_STATUSES),
        )
    ).all()
    requeued_ids: list[int] = []
    for failure in failures:
        failure.status = "queued"
        failure.attempt_count = 0
        failure.next_retry_at = now
        requeued_ids.append(int(failure.lot_id))

    seen = _p9_canary_seen_ids(session)
    seen.update(requeued_ids)
    marker = session.scalar(select(AppSetting).where(AppSetting.key == _P9_CANARY_SETTING_KEY))
    payload = json.dumps(
        {
            "version": _P9_CANARY_VERSION,
            "updated_at": now.isoformat(),
            "lot_ids": sorted(seen),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if marker is None:
        marker = AppSetting(key=_P9_CANARY_SETTING_KEY, value=payload)
        session.add(marker)
    else:
        marker.value = payload
    session.commit()
    return {
        **plan,
        "requeued": len(requeued_ids),
        "requeued_sample_lot_ids": sorted(requeued_ids)[:25],
        "requeued_lot_ids": sorted(requeued_ids),
    }


@dataclass(frozen=True, slots=True)
class GeoWorkItem:
    lot_id: int
    cadastral_number: str | None
    cadastral_numbers: list[str] | None
    address: str | None
    title: str | None
    description: str | None
    region_name: str | None
    region_code: str | None


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
        "region_code": str(item.region_code or "").strip().zfill(2),
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
    """Count the coordinates currently used by the map, not historical snapshots."""
    rows = session.execute(
        select(
            ProcessedLot.current_geo_source,
            ProcessedLot.current_geo_confidence,
            func.count(),
        ).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.is_not(None),
            ProcessedLot.current_geo_lon.is_not(None),
        ).group_by(
            ProcessedLot.current_geo_source,
            ProcessedLot.current_geo_confidence,
        )
    ).all()
    active = int(
        session.scalar(
            select(func.count()).where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
            )
        )
        or 0
    )
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
        select(
            ProcessedLot.id,
            ProcessedLot.address,
            ProcessedLot.cadastral_number,
            ProcessedLot.region_name,
            ProcessedLot.region_code,
            LotGeoSnapshot,
        )
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
    low_quality_ids: list[int] = []
    quality_scores: list[int] = []
    for lot_id, address, cadastral_number, region_name, region_code, snapshot in rows:
        lat, lon = float(snapshot.centroid_lat), float(snapshot.centroid_lon)
        if not (41.0 <= lat <= 82.0 and 19.0 <= lon <= 180.0):
            invalid_ids.append(lot_id)
        hotspots[(round(lat, 4), round(lon, 4))].append(lot_id)
        expected = expected_locality_name(address)
        observed = str((snapshot.metadata_json or {}).get("address") or "").casefold()
        if expected and observed and expected not in observed:
            locality_mismatch_ids.append(lot_id)

        metadata = dict(snapshot.metadata_json or {})
        score_value = metadata.get("quality_score")
        if score_value is None:
            reconstructed = CadastralObjectResult(
                query=str(metadata.get("query") or ""),
                cadastral_number=metadata.get("cadastral_number"),
                lat=lat,
                lon=lon,
                source=str(snapshot.geo_source or metadata.get("source") or ""),
                confidence=str(snapshot.geo_confidence or "unknown"),
                address=metadata.get("address"),
            )
            score = geocoding_result_quality_score(
                reconstructed,
                cadastral_number=cadastral_number,
                address=address,
                region_name=region_name,
                region_code=region_code,
            )
        else:
            try:
                score = int(score_value)
            except (TypeError, ValueError):
                score = 0
        quality_scores.append(score)
        if score < 60:
            low_quality_ids.append(lot_id)
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
        "quality_score_count": len(quality_scores),
        "average_quality_score": (
            round(sum(quality_scores) / len(quality_scores), 1)
            if quality_scores
            else None
        ),
        "low_quality_score_count": len(low_quality_ids),
        "low_quality_score_sample_lot_ids": low_quality_ids[:50],
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
    has_geo = (
        ProcessedLot.current_geo_lat.is_not(None)
        & ProcessedLot.current_geo_lon.is_not(None)
    )

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
        "backlog": geocoding_backlog_classification(session),
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


def geocoding_backlog_classification(session: Any) -> dict[str, Any]:
    """Classify active unmapped lots without mixing deferred work into runnable backlog."""
    current = utc_now()
    now = current.astimezone(timezone.utc).replace(tzinfo=None) if current.tzinfo is not None else current
    population = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
    )
    current_missing = or_(
        ProcessedLot.current_geo_lat.is_(None),
        ProcessedLot.current_geo_lon.is_(None),
    )
    rows = session.execute(
        select(
            ProcessedLot.id,
            ProcessedLot.cadastral_number,
            ProcessedLot.address,
            GeoFailure.status,
            GeoFailure.next_retry_at,
            GeoFailure.error_message,
        )
        .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(*population, current_missing)
    ).all()

    by_input = {
        "cadastre_and_address": 0,
        "cadastre_only": 0,
        "address_only": 0,
        "no_geocoding_input": 0,
    }
    retry_state = {
        "eligible_now": 0,
        "waiting_for_retry": 0,
        "network_wait": 0,
        "p7_held": 0,
        "deferred_no_match": 0,
        "deferred_validation": 0,
        "terminal": 0,
        "no_geocoding_input": 0,
    }
    reasons: Counter[str] = Counter()
    samples: dict[str, list[int]] = {key: [] for key in retry_state}

    for lot_id, cadastral_number, address, status, next_retry_at, error_message in rows:
        has_cadastre = bool(str(cadastral_number or "").strip())
        has_address = bool(str(address or "").strip())
        if has_cadastre and has_address:
            by_input["cadastre_and_address"] += 1
        elif has_cadastre:
            by_input["cadastre_only"] += 1
        elif has_address:
            by_input["address_only"] += 1
        else:
            by_input["no_geocoding_input"] += 1
            state = "no_geocoding_input"
            retry_state[state] += 1
            if len(samples[state]) < 25:
                samples[state].append(int(lot_id))
            continue

        retry_at = next_retry_at
        if retry_at is not None and retry_at.tzinfo is not None:
            retry_at = retry_at.astimezone(timezone.utc).replace(tzinfo=None)
        if status in _DEFERRED_STATUSES or status == "terminal":
            state = str(status)
        elif status == "network_wait":
            state = "network_wait"
        elif status == "p7_queued" and retry_at is not None and retry_at > now:
            state = "p7_held"
        elif retry_at is not None and retry_at > now:
            state = "waiting_for_retry"
        else:
            state = "eligible_now"
        retry_state[state] += 1
        if len(samples[state]) < 25:
            samples[state].append(int(lot_id))
        if error_message:
            reasons[_failure_reason_from_message(str(error_message))] += 1

    actionable = retry_state["eligible_now"] + retry_state["waiting_for_retry"]
    return {
        "unmapped_active_lots": len(rows),
        "actionable_remaining": actionable,
        "by_input": by_input,
        "retry_state": retry_state,
        "top_failure_reasons": dict(reasons.most_common(25)),
        "sample_lot_ids": samples,
    }

def geocoding_progress(session: Any) -> dict[str, Any]:
    """Return truthful GEO coverage, runnable work, deferred work and retry-wait state."""
    population = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        or_(ProcessedLot.cadastral_number.isnot(None), ProcessedLot.address.isnot(None)),
    )
    has_geo = ProcessedLot.current_geo_lat.is_not(None) & ProcessedLot.current_geo_lon.is_not(None)
    pending = or_(
        ~has_geo,
        (ProcessedLot.needs_geo_check.is_(True) & ProcessedLot.geo_input_hash.is_(None)),
    )
    now = utc_now()
    excluded = ("terminal", *_DEFERRED_STATUSES)

    total = int(session.scalar(select(func.count()).select_from(ProcessedLot).where(*population)) or 0)
    geocoded = int(session.scalar(select(func.count()).select_from(ProcessedLot).where(*population, has_geo)) or 0)

    def _status_count(status: str) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ProcessedLot)
                .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
                .where(
                    *population,
                    pending,
                    GeoFailure.status == status,
                    *(
                        (ProcessedLot.geo_input_hash.is_not(None),)
                        if status in _DEFERRED_STATUSES
                        else ()
                    ),
                )
            )
            or 0
        )

    terminal = _status_count("terminal")
    deferred_no_match = _status_count("deferred_no_match")
    deferred_validation = _status_count("deferred_validation")
    network_wait = _status_count("network_wait")
    p7_held = int(
        session.scalar(
            select(func.count())
            .select_from(ProcessedLot)
            .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                *population,
                pending,
                GeoFailure.status == "p7_queued",
                GeoFailure.next_retry_at.is_not(None),
                GeoFailure.next_retry_at > now,
            )
        )
        or 0
    )

    eligible_now = int(
        session.scalar(
            select(func.count())
            .select_from(ProcessedLot)
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                *population,
                pending,
                or_(
                    GeoFailure.id.is_(None),
                    and_(
                        GeoFailure.status.not_in(excluded),
                        or_(GeoFailure.next_retry_at.is_(None), GeoFailure.next_retry_at <= now),
                    ),
                    and_(
                        GeoFailure.status.in_(_DEFERRED_STATUSES),
                        ProcessedLot.geo_input_hash.is_(None),
                    ),
                ),
            )
        )
        or 0
    )
    waiting_for_retry = int(
        session.scalar(
            select(func.count())
            .select_from(ProcessedLot)
            .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                *population,
                pending,
                GeoFailure.status.not_in((*excluded, "network_wait", "p7_queued")),
                GeoFailure.next_retry_at.is_not(None),
                GeoFailure.next_retry_at > now,
            )
        )
        or 0
    )
    next_retry_at = session.scalar(
        select(func.min(GeoFailure.next_retry_at))
        .select_from(ProcessedLot)
        .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(
            *population,
            pending,
            GeoFailure.status.not_in((*excluded, "network_wait", "p7_queued")),
            GeoFailure.next_retry_at.is_not(None),
            GeoFailure.next_retry_at > now,
        )
    )

    latest = session.scalar(
        select(BackgroundTaskState)
        .where(BackgroundTaskState.task_type == "geocoding")
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(1)
    )
    recent = session.scalars(
        select(BackgroundTaskState)
        .where(BackgroundTaskState.task_type == "geocoding", BackgroundTaskState.status == "completed")
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

    actionable_remaining = eligible_now + waiting_for_retry
    if actionable_remaining == 0:
        eta_seconds = 0
    elif eligible_now > 0 and rate:
        eta_seconds = math.ceil(eligible_now / rate)
    else:
        eta_seconds = None

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

    deferred_bad_input = int(
        session.scalar(
            select(func.count())
            .select_from(ProcessedLot)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                or_(ProcessedLot.current_geo_lat.is_(None), ProcessedLot.current_geo_lon.is_(None)),
                ProcessedLot.cadastral_number.is_(None),
                ProcessedLot.address.is_(None),
            )
        )
        or 0
    )
    fast_drain_row = session.scalar(
        select(BackgroundTaskState)
        .where(BackgroundTaskState.task_type == "geocoding_fast_drain")
        .order_by(BackgroundTaskState.created_at.desc(), BackgroundTaskState.id.desc())
        .limit(1)
    )

    paused = is_geocoding_paused(session)
    deferred = deferred_no_match + deferred_validation
    classified = min(total, geocoded + terminal + deferred)
    return {
        "total": total,
        "geocoded": geocoded,
        "remaining": max(0, total - geocoded),
        "terminal_failures": terminal,
        "deferred_no_match": deferred_no_match,
        "deferred_validation": deferred_validation,
        "deferred_total": deferred,
        "network_wait": network_wait,
        "p7_held": p7_held,
        "deferred_bad_input": deferred_bad_input,
        "drain_remaining": actionable_remaining + p7_held,
        "classified": classified,
        "classified_percent": round((classified / total * 100) if total else 100.0, 1),
        "resolved": classified,
        "resolved_percent": round((classified / total * 100) if total else 100.0, 1),
        "actionable_remaining": actionable_remaining,
        "eligible_now": eligible_now,
        "waiting_for_retry": waiting_for_retry,
        "next_retry_at": next_retry_at.isoformat() if next_retry_at is not None else None,
        "percent": round((geocoded / total * 100) if total else 100.0, 1),
        "paused": paused,
        "rate_per_second": round(rate, 3) if rate else None,
        "eta_seconds": eta_seconds,
        "eta_scope": "eligible_now" if eta_seconds not in (None, 0) else None,
        "expected_completion_at": (datetime.now(timezone.utc) + timedelta(seconds=eta_seconds)).isoformat()
        if eta_seconds is not None and eta_seconds > 0 and not paused
        else None,
        "drain_eta_seconds": (
            math.ceil((eligible_now + p7_held) / rate)
            if rate and (eligible_now + p7_held) > 0 and not paused
            else 0 if (eligible_now + p7_held) == 0 else None
        ),
        "elapsed_seconds": elapsed_seconds,
        "estimated_total_seconds": (elapsed_seconds + eta_seconds)
        if elapsed_seconds is not None and eta_seconds is not None
        else None,
        "network": network_health_snapshot(),
        "fast_drain": None
        if fast_drain_row is None
        else {
            "task_id": fast_drain_row.task_id,
            "status": fast_drain_row.status,
            "progress": fast_drain_row.progress_json,
            "result": fast_drain_row.result_json,
            "error": fast_drain_row.error_message,
            "started_at": fast_drain_row.started_at,
            "finished_at": fast_drain_row.finished_at,
        },
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

def _failure_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, GeoProviderUnavailable):
        return {
            "error": str(value),
            "attempts": [{
                "source": value.provider,
                "valid": False,
                "reason": value.category,
                "operational": True,
            }],
        }
    if isinstance(value, Exception):
        return {
            "error": str(value)[:500],
            "attempts": [{
                "source": "runtime",
                "valid": False,
                "reason": f"exception:{value.__class__.__name__}",
            }],
        }
    if value is None:
        return {"error": "Geocoding chain returned no result", "attempts": []}
    return {
        "error": getattr(value, "error", None) or "No validated coordinates",
        "attempts": getattr(value, "attempts", None) or [],
    }


def _classify_geo_failure(value: Any) -> str:
    payload = _failure_payload(value)
    attempts = [item for item in payload.get("attempts", []) if isinstance(item, dict)]
    reasons = [str(item.get("reason") or "") for item in attempts]
    if any(bool(item.get("operational")) or is_operational_category(item.get("reason")) for item in attempts):
        return "operational"
    if isinstance(value, GeoProviderUnavailable):
        return "operational"
    if isinstance(value, Exception):
        return "internal"
    if any(
        reason.endswith("_mismatch")
        or reason in {"low_confidence", "coordinates_out_of_range"}
        for reason in reasons
    ):
        return "validation"
    if not reasons or all(reason == "no_coordinates" for reason in reasons):
        return "no_match"
    if "No validated" in str(payload.get("error") or ""):
        return "no_match"
    return "internal"


def _operational_attempt_count(error_message: str | None) -> int:
    try:
        payload = json.loads(error_message or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    meta = payload.get("meta") if isinstance(payload, dict) else None
    return int((meta or {}).get("operational_attempt_count") or 0) if isinstance(meta, dict) else 0


def _record_classified_failure(session: Any, lot_id: int, value: Any) -> str:
    classification = _classify_geo_failure(value)
    payload = _failure_payload(value)
    existing = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))

    if classification == "operational":
        operational_attempt = _operational_attempt_count(existing.error_message if existing else None) + 1
        reason = next(
            (
                str(item.get("reason"))
                for item in payload.get("attempts", [])
                if isinstance(item, dict) and (item.get("operational") or is_operational_category(item.get("reason")))
            ),
            "connection_error",
        )
        delay = retry_delay_seconds(reason, operational_attempt) or 900
        payload["classification"] = "operational"
        payload["meta"] = {"operational_attempt_count": operational_attempt}
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:2000]
        if existing is None:
            existing = GeoFailure(
                lot_id=lot_id,
                status="network_wait",
                attempt_count=0,
                error_message=message,
                last_failed_at=utc_now(),
                next_retry_at=utc_now() + timedelta(seconds=delay),
            )
            session.add(existing)
        else:
            existing.status = "network_wait"
            existing.error_message = message
            existing.last_failed_at = utc_now()
            existing.next_retry_at = utc_now() + timedelta(seconds=delay)
            existing.resolved_at = None
        return f"operational:{reason}"[:160]

    if existing is not None and existing.status in _DEFERRED_STATUSES:
        existing.status = "queued"
        existing.attempt_count = 0
        existing.next_retry_at = utc_now()

    category = "no_match" if classification == "no_match" else "validation" if classification == "validation" else "internal"
    payload["classification"] = category
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:2000]
    failure = record_geo_failure(session, lot_id, message, retry_after_seconds=0)
    semantic_attempt = int(failure.attempt_count or 1)
    semantic_delay = retry_delay_seconds(category, semantic_attempt)
    if semantic_delay is None:
        if category == "no_match":
            failure.status = "deferred_no_match"
        elif category == "validation":
            failure.status = "deferred_validation"
        else:
            failure.status = "terminal"
        failure.next_retry_at = None
    else:
        failure.status = "queued"
        failure.next_retry_at = utc_now() + timedelta(seconds=semantic_delay)
    return category


def _record_scheduled_failure(session: Any, lot_id: int, error: str) -> None:
    """Backward-compatible bounded internal failure recording used by persistence fallback."""
    _record_classified_failure(session, lot_id, RuntimeError(error))


def _geocoding_failure_message(value: Any) -> str:
    return json.dumps(_failure_payload(value), ensure_ascii=False, separators=(",", ":"))[:2000]


def _geocoding_failure_reason(value: Any) -> str:
    """Return a bounded aggregate label without leaking an address or query."""
    classification = _classify_geo_failure(value)
    if classification == "operational":
        payload = _failure_payload(value)
        for attempt in reversed(payload.get("attempts", [])):
            if not isinstance(attempt, dict):
                continue
            reason = attempt.get("reason")
            source = attempt.get("source")
            if reason and (attempt.get("operational") or is_operational_category(reason)):
                return f"{source or 'provider'}:{reason}"[:160]
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
    existing = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
    if existing is not None and existing.status in _DEFERRED_STATUSES and lot.geo_input_hash is None:
        existing.status = "queued"
        existing.attempt_count = 0
        existing.next_retry_at = utc_now()

    if not isinstance(value, Exception) and apply_lot_geo_result(session, lot, value):
        lot.geo_input_hash = current_input_hash
        resolve_geo_failure(session, lot_id)
        return True, (value.source or "unknown")

    lot.geo_input_hash = current_input_hash
    _record_classified_failure(session, lot_id, value)
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
    lot_ids: Sequence[int] | None = None,
    allow_when_paused: bool = False,
    refresh_strategy: bool = True,
) -> dict[str, Any]:
    """Geocode a bounded production batch without holding a DB transaction during the whole run."""
    started_at = time.monotonic()
    target_lot_ids = tuple(sorted({int(value) for value in (lot_ids or ()) if int(value) > 0}))
    with session_factory() as session:
        if is_geocoding_paused(session) and not allow_when_paused:
            paused_result = {
                "status": "paused",
                "paused": True,
                "phase": "paused",
                "queued": 0,
                "processed": 0,
                "geocoded": 0,
                "failed": 0,
                "percent": 0.0,
                "strategy_version": _GEO_STRATEGY_VERSION,
                "strategy_requeued": 0,
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
        strategy_requeued = (
            _refresh_failures_for_current_strategy(session)
            if refresh_strategy and not target_lot_ids
            else 0
        )
        latest_geo_id = (
            select(func.max(LotGeoSnapshot.id))
            .where(LotGeoSnapshot.lot_id == ProcessedLot.id)
            .correlate(ProcessedLot)
            .scalar_subquery()
        )
        current_geo_missing = or_(
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
        )
        pending_filter = (
            or_(
                current_geo_missing,
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
                current_geo_missing,
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
                ProcessedLot.region_code,
            )
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                *(
                    (ProcessedLot.id.in_(target_lot_ids),)
                    if target_lot_ids
                    else ()
                ),
                or_(
                    ProcessedLot.cadastral_number.isnot(None),
                    ProcessedLot.address.isnot(None),
                ),
                pending_filter,
                or_(
                    GeoFailure.id.is_(None),
                    and_(
                        GeoFailure.status.not_in(("terminal", *_DEFERRED_STATUSES)),
                        or_(GeoFailure.next_retry_at.is_(None), GeoFailure.next_retry_at <= now),
                    ),
                    and_(
                        GeoFailure.status.in_(_DEFERRED_STATUSES),
                        ProcessedLot.geo_input_hash.is_(None),
                    ),
                ),
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
                region_code=values[0].region_code,
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
    lot_ids: Sequence[int] | None = None,
    allow_when_paused: bool = False,
    refresh_strategy: bool = True,
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
                lot_ids=lot_ids,
                allow_when_paused=allow_when_paused,
                refresh_strategy=refresh_strategy,
            )
        with _distributed_geo_lock():
            return _geocode_pending_lots_unlocked(
                session_factory,
                limit=limit,
                re_geocode_existing=re_geocode_existing,
                progress_task_id=progress_task_id,
                lot_ids=lot_ids,
                allow_when_paused=allow_when_paused,
                refresh_strategy=refresh_strategy,
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
                ProcessedLot.region_code,
                GeoFailure.error_message,
                GeoFailure.attempt_count,
            )
            .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                ProcessedLot.cadastral_number.is_not(None),
                GeoFailure.status.in_(("queued", "terminal", "deferred_no_match")),
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
                region_code=row.region_code,
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
                region_code=lot.region_code,
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
