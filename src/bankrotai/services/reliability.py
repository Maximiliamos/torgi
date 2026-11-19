"""Read-only aggregate model for the Reliability dashboard."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import requests

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from bankrotai import __version__
from bankrotai.core import get_settings
from bankrotai.db import AppSetting, MapDataset, ProcessedLot, SCHEMA_REVISION, utc_now
from bankrotai.services.geo_backfill import geocoding_progress
from bankrotai.services.operations_status import operations_host_status
from bankrotai.services.quality import (
    list_source_health,
    map_delivery_reconciliation_report,
    operational_quality_report,
)
from bankrotai.services.map_payload import ENDED_STATUSES


_SOURCE_ALIASES = {
    "tbankrot": "tbankrot.ru",
    "tbankrot.ru": "tbankrot.ru",
    "гис торги": "torgi.gov.ru",
    "torgi.gov.ru": "torgi.gov.ru",
    "рад / лот-онлайн": "lot-online.ru",
    "рад/лот-онлайн": "lot-online.ru",
    "lot-online.ru": "lot-online.ru",
}
_SOURCE_DISPLAY_RU = {
    "torgi.gov.ru": "ГИС Торги",
    "tbankrot.ru": "TBankrot",
    "lot-online.ru": "ЛОТ-ОНЛАЙН",
    "torgi-russia.ru": "Torgi Russia",
    "bidexpert.ru": "BidExpert",
    "m-ets.ru": "M-ETS",
    "cdtrf.ru": "ЦДТ РФ",
    "nistp.ru": "НИСТП",
    "sberbank-ast.ru": "Сбербанк-АСТ",
    "vertrades.ru": "Vertrades",
}


_HEAVY_CACHE_LOCK = threading.Lock()
_HEAVY_CACHE_TTL_SECONDS = 300.0
_HEAVY_CACHE: dict[str, Any] = {"at": 0.0, "operational": None, "map": None}

# The dashboard refreshes every 30s. Reuse the same consistent snapshot
# briefly rather than repeating expensive aggregate SQL on every browser poll.
# This cache is per-process, bounded to one snapshot, and never written to DB.
_SUMMARY_CACHE_LOCK = threading.Lock()
_SUMMARY_CACHE_TTL_SECONDS = 45.0
_SUMMARY_CACHE: dict[str, Any] = {"at": 0.0, "snapshot": None}


def _summary_cache_enabled(session: Session) -> bool:
    bind = session.get_bind()
    return bool(
        bind is not None
        and bind.dialect.name == "postgresql"
        and get_settings().is_production
    )



def _quick_public_manifest(dataset: dict[str, Any] | None) -> dict[str, Any]:
    settings = get_settings()
    version = str((dataset or {}).get("version") or "")
    if not version or not settings.map_object_store_public_base_url:
        return {"checked": False, "ok": None, "reason": "public_manifest_not_configured"}
    url = (
        f"{settings.map_object_store_public_base_url.rstrip('/')}"
        f"/datasets/{quote(version, safe='-_.~')}/manifest.json"
    )
    try:
        response = requests.get(
            url,
            headers={"Origin": "https://sterdez.online", "Cache-Control": "no-cache"},
            timeout=min(float(settings.map_object_store_timeout_seconds), 3.0),
        )
        if response.status_code != 200:
            return {"checked": True, "ok": False, "http_status": response.status_code}
        payload = response.json()
        expected_points = int((dataset or {}).get("declared_point_count") or 0)
        expected_tiles = int((dataset or {}).get("declared_tile_count") or 0)
        ok = (
            payload.get("version") == version
            and int(payload.get("point_count", -1)) == expected_points
            and int(payload.get("tile_count", -1)) == expected_tiles
        )
        return {
            "checked": True,
            "ok": ok,
            "version": payload.get("version"),
            "point_count": payload.get("point_count"),
            "tile_count": payload.get("tile_count"),
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {"checked": True, "ok": False, "error": f"{exc.__class__.__name__}: public_manifest_request_failed"}


# Public object-store checks are performed in a short-lived background daemon,
# never inside the interactive /api/reliability/summary request. The page remains
# explicitly 'not checked' until a result for the current dataset is available.
_MANIFEST_CACHE_LOCK = threading.Lock()
_MANIFEST_CACHE: dict[str, Any] = {
    "at": 0.0, "key": "", "result": None, "running": False,
}


def _refresh_manifest(dataset: dict[str, Any], key: str) -> None:
    try:
        result = _quick_public_manifest(dataset)
    except Exception:
        result = {"checked": True, "ok": False, "reason": "public_manifest_check_failed"}
    with _MANIFEST_CACHE_LOCK:
        if _MANIFEST_CACHE["key"] == key:
            _MANIFEST_CACHE.update({
                "at": time.monotonic(), "result": result, "running": False,
            })


def _cached_public_manifest(dataset: dict[str, Any] | None) -> dict[str, Any]:
    version = str((dataset or {}).get("version") or "")
    base_url = get_settings().map_object_store_public_base_url or ""
    if not version or not base_url:
        return {"checked": False, "ok": None, "reason": "public_manifest_not_configured"}
    key = f"{base_url.rstrip('/')}/{version}"
    launch = False
    with _MANIFEST_CACHE_LOCK:
        if _MANIFEST_CACHE["key"] != key:
            _MANIFEST_CACHE.update({"key": key, "at": 0.0, "result": None, "running": False})
        result = _MANIFEST_CACHE.get("result")
        if isinstance(result, dict) and time.monotonic() - float(_MANIFEST_CACHE["at"]) < 300:
            return deepcopy(result)
        if not _MANIFEST_CACHE["running"]:
            _MANIFEST_CACHE["running"] = True
            launch = True
    if launch:
        try:
            threading.Thread(
                target=_refresh_manifest, args=(dict(dataset or {}), key),
                daemon=True, name="reliability-public-manifest",
            ).start()
        except RuntimeError:
            with _MANIFEST_CACHE_LOCK:
                if _MANIFEST_CACHE["key"] == key:
                    _MANIFEST_CACHE["running"] = False
    return {"checked": False, "ok": None, "reason": "public_manifest_verification_pending"}


def _dataset_cache_identity(session: Session):
    rows = session.scalars(select(MapDataset).where(MapDataset.is_current.is_(True)).order_by(MapDataset.id)).all()
    key = tuple((r.id, r.version, r.point_count, r.tile_count, r.status, r.published_at) for r in rows)
    dataset = None if len(rows) != 1 else {
        "version": rows[0].version, "status": rows[0].status,
        "declared_point_count": rows[0].point_count, "declared_tile_count": rows[0].tile_count,
        "published_at": rows[0].published_at,
    }
    return key, dataset, len(rows)


def _compute_quality_bundle(session: Session):
    checked_at = utc_now().isoformat()
    operational = operational_quality_report(session, stale_days=7, problem_limit=10)
    report = map_delivery_reconciliation_report(session, verify_public_manifest=False, problem_limit=10)
    return {**operational, "checked_at": checked_at}, {**report, "checked_at": checked_at}


def _refresh_quality_bundle(dataset_key):
    from bankrotai.db import SessionLocal
    try:
        with SessionLocal() as session:
            prepare_reliability_snapshot(session)
            if _dataset_cache_identity(session)[0] != dataset_key:
                return
            captured_at = time.monotonic()
            operational, report = _compute_quality_bundle(session)
        with _HEAVY_CACHE_LOCK:
            _HEAVY_CACHE.update({"at": captured_at, "operational": operational,
                                 "map": report, "dataset_key": dataset_key, "retry_at": 0.0,
                                 "last_error_key": None})
    except Exception:
        with _HEAVY_CACHE_LOCK:
            _HEAVY_CACHE["retry_at"] = time.monotonic() + 30.0
            _HEAVY_CACHE["last_error_key"] = dataset_key
    finally:
        with _HEAVY_CACHE_LOCK:
            _HEAVY_CACHE["running"] = False


def _heavy_quality_bundle(session: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    now = time.monotonic()
    dataset_key, dataset, dataset_count = _dataset_cache_identity(session)
    asynchronous = _summary_cache_enabled(session)
    launch = False
    with _HEAVY_CACHE_LOCK:
        operational = _HEAVY_CACHE.get("operational")
        report = _HEAVY_CACHE.get("map")
        same_dataset = (_HEAVY_CACHE.get("dataset_key") == dataset_key
                        and isinstance(operational, dict) and isinstance(report, dict))
        fresh = same_dataset and now - float(_HEAVY_CACHE.get("at") or 0.0) < _HEAVY_CACHE_TTL_SECONDS
        if not fresh and not asynchronous:
            operational, report = _compute_quality_bundle(session)
            _HEAVY_CACHE.update({"at": time.monotonic(), "operational": operational,
                                 "map": report, "dataset_key": dataset_key, "last_error_key": None})
            fresh = same_dataset = True
        elif not fresh and not _HEAVY_CACHE.get("running") and now >= float(_HEAVY_CACHE.get("retry_at") or 0):
            _HEAVY_CACHE["running"] = launch = True
        if not same_dataset:
            operational = {"integrity": {"current_dataset_count": dataset_count}}
            report = {"ok": False, "dataset": dataset}
        assert isinstance(operational, dict) and isinstance(report, dict)
        operational, report = deepcopy(operational), deepcopy(report)
        verification_error = _HEAVY_CACHE.get("last_error_key") == dataset_key
    if launch:
        try:
            threading.Thread(target=_refresh_quality_bundle, args=(dataset_key,),
                             daemon=True, name="reliability-quality-snapshot").start()
        except RuntimeError:
            with _HEAVY_CACHE_LOCK:
                _HEAVY_CACHE.update({"running": False, "retry_at": now + 30.0})
    return operational, {
        **report, "verification_pending": not fresh, "verification_stale": same_dataset and not fresh,
        "verification_error": verification_error,
        "manifest": _cached_public_manifest(report.get("dataset")) if fresh
                    else {"checked": False, "ok": None, "reason": "quality_verification_pending"},
    }


def _canonical_source(value: str) -> str:
    raw = (value or "").strip()
    return _SOURCE_ALIASES.get(raw.casefold(), raw.casefold())


def _safe_error(message: str | None) -> str | None:
    """Keep diagnostic messages short and remove credential-like values."""
    if not message:
        return None
    value = str(message)[:500]
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+",
                   r"\1[скрыто]", value)
    value = re.sub(r"(?im)(\b(?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]+", r"\1[скрыто]", value)
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[скрыто]@", value)
    value = re.sub(r'''(?i)(["']?(?:password|token|api[_-]?key|secret)["']?\s*[:=]\s*)(["'])(.*?)\2''',
                   r"\1[скрыто]", value)
    value = re.sub(
        r"(?i)(authorization|cookie|password|token|api[_-]?key|secret)\s*[:=]\s*[^\s;&]+",
        r"\1=[скрыто]",
        value,
    )
    value = re.sub(
        r"(?i)([?&](?:token|key|password|access_token|signature|secret)=)[^&#\s]+",
        r"\1[скрыто]",
        value,
    )
    return value[:300]

def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _retry_overdue(item: Any, now: datetime, *, paused: bool = False) -> bool:
    """A scheduled retry is overdue only until an actual subsequent attempt."""
    next_retry = _utc_naive(getattr(item, "next_retry_at", None))
    current = _utc_naive(now) or now
    if paused or next_retry is None or next_retry >= current:
        return False
    if getattr(item, "status", "") in {"running", "queued"}:
        return False
    return not any(
        (attempt := _utc_naive(getattr(item, field, None))) is not None
        and attempt >= next_retry
        for field in ("last_attempt_at", "last_success_at")
    )


def _current_error(item: Any) -> str | None:
    if not item.last_error or not item.last_failure_at:
        return None
    if item.last_success_at and item.last_success_at >= item.last_failure_at:
        return None
    return item.last_error


def _source_status(item: Any, *, paused: bool = False, retry_overdue: bool = False) -> str:
    if paused:
        return "paused"
    if (
        item.status == "not_checked"
        and getattr(item, "last_attempt_at", None) is None
        and getattr(item, "last_success_at", None) is None
    ):
        return "not_checked"
    if item.status == "failed" or _current_error(item):
        return "failed"
    if (
        item.circuit_state in {"open", "half_open"}
        or retry_overdue
        or item.status == "partial"
        or item.freshness_status != "fresh"
        or item.coverage_status != "fresh"
    ):
        return "degraded"
    if item.status in {"healthy", "success"} and item.freshness_status == "fresh":
        return "healthy"
    return "not_checked"


def reliability_sources(session: Session) -> dict[str, Any]:
    from bankrotai.services.ingestion import default_source_specs
    from bankrotai.connectors.registry import connector_registry
    configured = {spec.source_id for spec in default_source_specs()}
    registered = set(connector_registry.source_ids())
    paused = {
        _canonical_source(str(row.key).removeprefix("source_paused:"))
        for row in session.scalars(
            select(AppSetting).where(AppSetting.key.like("source_paused:%"))
        ).all()
        if str(row.value or "").strip().casefold() in {"1", "true", "yes", "on"}
    }

    grouped: dict[str, list[Any]] = {}
    aliases: dict[str, set[str]] = {}
    for item in list_source_health(session):
        canonical = _canonical_source(item.source_system)
        grouped.setdefault(canonical, []).append(item)
        aliases.setdefault(canonical, set()).add(item.source_system)

    generated_at = utc_now()
    all_ids = sorted(set(grouped) | configured | registered | paused)
    rows: list[dict[str, Any]] = []
    for source_id in all_ids:
        members = grouped.get(source_id, [])
        if members:
            # Prefer the newest attempt; aliases remain visible only in details.
            item = max(
                members,
                key=lambda value: value.last_attempt_at or value.last_success_at or datetime.min,
            )
            current_error = _current_error(item)
            retry_overdue = _retry_overdue(item, generated_at, paused=source_id in paused)
            overall = _source_status(item, paused=source_id in paused, retry_overdue=retry_overdue)
            row = {
                **item.model_dump(mode="json"),
                "canonical_id": source_id,
                "display_name_ru": _SOURCE_DISPLAY_RU.get(source_id, source_id),
                "aliases": sorted(aliases.get(source_id, set())),
                "configured": source_id in configured,
                "scheduled": source_id in configured,
                "effective_scheduled": source_id in configured and source_id not in paused,
                "pilot": source_id in registered and source_id not in configured,
                "registered": source_id in registered,
                "historical_only": source_id not in configured and source_id not in registered,
                "paused": source_id in paused,
                "overall_status": overall,
                "current_error": _safe_error(current_error),
                "last_error": _safe_error(current_error),
                "last_historical_error": _safe_error(item.last_historical_error),
                "network_fingerprint": {},
                "retry_overdue": retry_overdue,
            }
        else:
            row = {
                "source_system": source_id,
                "canonical_id": source_id,
                "display_name_ru": _SOURCE_DISPLAY_RU.get(source_id, source_id),
                "aliases": [],
                "configured": source_id in configured,
                "scheduled": source_id in configured,
                "effective_scheduled": source_id in configured and source_id not in paused,
                "pilot": source_id in registered and source_id not in configured,
                "registered": source_id in registered,
                "historical_only": source_id not in configured and source_id not in registered,
                "paused": source_id in paused,
                "overall_status": "paused" if source_id in paused else "not_checked",
                "current_error": None,
                "last_historical_error": None,
                "retry_overdue": False,
                "status": "not_checked",
                "items_seen": 0,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_complete_success_at": None,
                "last_failure_at": None,
                "last_error": None,
                "last_error_category": None,
                "circuit_state": "closed",
                "next_retry_at": None,
                "freshness_status": "stale",
                "coverage_status": "missing",
                "last_pages_scanned": 0,
                "last_items_inserted": 0,
                "last_items_updated": 0,
                "last_items_archived": 0,
                "last_items_failed": 0,
            }
        full_at = _utc_naive(getattr(item, "last_complete_success_at", None)) if members else None
        row["full_snapshot_age_seconds"] = max(0, int((generated_at - full_at).total_seconds())) if full_at else None
        rows.append(row)

    order = {"failed": 0, "degraded": 1, "paused": 2, "not_checked": 3, "healthy": 4}
    rows.sort(key=lambda value: (order.get(str(value["overall_status"]), 9), str(value["display_name_ru"]).casefold()))
    return {
        "generated_at": generated_at.isoformat(),
        "configured_total": len(configured),
        "scheduled_total": sum(bool(row["scheduled"]) for row in rows),
        "effective_scheduled_total": sum(bool(row["effective_scheduled"]) for row in rows),
        "registered_total": len(registered),
        "monitored_total": len(rows),
        "paused_total": sum(bool(row["paused"]) for row in rows if row["configured"]),
        "healthy": sum(row["overall_status"] == "healthy" for row in rows if row["configured"]),
        "degraded": sum(row["overall_status"] == "degraded" for row in rows if row["configured"]),
        "failed": sum(row["overall_status"] == "failed" for row in rows if row["configured"]),
        "not_checked": sum(row["overall_status"] == "not_checked" for row in rows if row["configured"]),
        "stale": sum(row.get("freshness_status") == "stale" for row in rows if row["configured"]),
        "complete_fresh": sum(row.get("coverage_status") == "fresh" for row in rows if row["configured"]),
        "current_full_snapshot_age": max((row["full_snapshot_age_seconds"] for row in rows
            if row["configured"] and row["full_snapshot_age_seconds"] is not None), default=None),
        "current_full_snapshot_age_unit": "seconds",
        "items": rows,
    }


def _current_quality(session: Session) -> dict[str, Any]:
    live = (
        ProcessedLot.duplicate_of_id.is_(None)
        & ProcessedLot.is_archived.is_(False)
        & or_(ProcessedLot.auction_status.is_(None), ProcessedLot.auction_status.notin_(tuple(ENDED_STATUSES)))
    )

    missing = {
        "missing_address": ProcessedLot.address.is_(None) | (func.trim(ProcessedLot.address) == ""),
        "missing_cadastre": ProcessedLot.cadastral_number.is_(None) | (func.trim(ProcessedLot.cadastral_number) == ""),
        "missing_price": ProcessedLot.current_price.is_(None) & ProcessedLot.start_price.is_(None),
        "unknown_status": or_(ProcessedLot.auction_status.is_(None), ProcessedLot.auction_status == "unknown"),
        "missing_region": ProcessedLot.region_code.is_(None) | (func.trim(ProcessedLot.region_code) == ""),
        "missing_url": (ProcessedLot.lot_url.is_(None) | (func.trim(ProcessedLot.lot_url) == ""))
                       & (ProcessedLot.source_url.is_(None) | (func.trim(ProcessedLot.source_url) == "")),
        "non_positive_price": (ProcessedLot.current_price.isnot(None) & (ProcessedLot.current_price <= 0))
                              | (ProcessedLot.start_price.isnot(None) & (ProcessedLot.start_price <= 0)),
    }
    row = session.execute(select(
        func.count().label("population"),
        *(func.count().filter(condition).label(name) for name, condition in missing.items()),
    ).select_from(ProcessedLot).where(live)).mappings().one()
    values: dict[str, Any] = {name: int(value or 0) for name, value in row.items()}
    total = values["population"]
    values["percentages"] = {
        key: round((value / total * 100) if total else 0.0, 1)
        for key, value in values.items()
        if key not in {"population", "percentages"}
    }
    return values


def _reliability_summary_uncached(session: Session) -> dict[str, Any]:
    sources = reliability_sources(session)
    geo_raw = geocoding_progress(session)
    # The shared GEO progress endpoint can include raw task errors, result payloads
    # and network fingerprints. The Reliability summary intentionally exposes only
    # whitelisted counts and bounded timing fields.
    safe_geo_fields = {
        "total", "geocoded", "remaining", "terminal_failures",
        "deferred_no_match", "deferred_validation", "deferred_total",
        "network_wait", "p7_held", "deferred_bad_input",
        "drain_remaining", "actionable_remaining", "eligible_now",
        "waiting_for_retry", "percent", "paused", "classified_percent",
    }
    geo = {key: value for key, value in geo_raw.items() if key in safe_geo_fields}
    geo["population"] = geo.get("total")
    geo["current_geocoded"] = geo.get("geocoded")
    geo["actionable"] = geo.get("actionable_remaining")
    geo["bad_input"] = geo.get("deferred_bad_input")
    geo["scope_description_ru"] = (
        "Текущие основные неархивные лоты с адресом или кадастровым номером. "
        "Показатель требует обработки может включать повторную проверку объектов с координатами."
    )
    host_raw = operations_host_status(session)
    disk_raw = host_raw.get("disk") or {}
    disk_free = disk_raw.get("free_gb")
    disk_status = (
        "unknown" if not isinstance(disk_free, (int, float))
        else "critical" if disk_free < 15
        else "warning" if disk_free < 25
        else "ok"
    )
    backup_raw = host_raw.get("backup") or {}
    runner_raw = host_raw.get("runner") or {}
    settings = get_settings()
    # API / DB readiness is supported by serving this authenticated database-backed request.
    # The background queue is not probed here; avoid claiming online readiness.
    host = {
        "api": {"status": "ok"},
        "database": {"status": "ok"},
        "queue": {"status": "disabled" if settings.api_read_only else "not_checked"},
        "read_only": settings.api_read_only,
        "disk": {
            "free_gb": disk_free,
            "checked_at": disk_raw.get("checked_at"),
            "status": disk_status,
            "critical_below_gb": 15,
            "recommended_gb": 25,
        },
        "backup_age_hours": host_raw.get("backup_age_hours"),
        "backup_healthy": host_raw.get("backup_healthy"),
        "backup": {
            "created_at": backup_raw.get("created_at"),
            "size_bytes": backup_raw.get("size_bytes"),
            "restore_verification": backup_raw.get("restore_verification"),
            "threshold_hours": 60,
        },
        "runner_age_seconds": host_raw.get("runner_age_seconds"),
        "runner_healthy": host_raw.get("runner_healthy"),
        "runner_diagnostic": {
            "checked_at": runner_raw.get("checked_at"),
            "status": "fresh" if host_raw.get("runner_healthy") else "stale",
        },
    }
    operational, map_report = _heavy_quality_bundle(session)
    current_quality = _current_quality(session)

    operational_integrity = operational.get("integrity") or {}
    integrity = {
        "source_without_processed": int(operational_integrity.get("source_without_processed") or 0),
        "processed_without_source": int(operational_integrity.get("processed_without_source") or 0),
        "current_dataset_count": int(operational_integrity.get("current_dataset_count") or 0),
        "tile_count_mismatches": int(operational_integrity.get("tile_count_mismatches") or 0),
        "active_and_archived": int(operational_integrity.get("active_and_archived_source_lots") or 0),
        "date_invariants": sum(
            int((operational.get("source_date_quality") or {}).get(key) or 0)
            for key in ("first_seen_after_last_seen", "application_start_after_deadline", "archived_before_first_seen")
        ),
    }
    manifest = map_report.get("manifest") or {}
    manifest_problem = manifest.get("checked") is True and manifest.get("ok") is not True
    manifest_unchecked = manifest.get("checked") is not True
    verification_pending = bool(map_report.get("verification_pending"))
    verification_stale = bool(map_report.get("verification_stale"))
    verification_error = bool(map_report.get("verification_error"))
    critical = (not verification_pending or verification_stale) and (
        integrity["current_dataset_count"] != 1
        or integrity["tile_count_mismatches"] > 0
        or bool(map_report.get("missing_from_dataset_count"))
        or bool(map_report.get("extra_in_dataset_count"))
        or (map_report.get("dataset") or {}).get("status") != "ready"
        or integrity["active_and_archived"] > 0
        or integrity["date_invariants"] > 0
    )
    warning_count = sum(
        value > 0
        for key, value in integrity.items()
        if key not in {"current_dataset_count"}
    )

    reasons: list[str] = []
    if verification_error:
        reasons.append("Сверка карты и целостности временно недоступна; запланирован повтор")
    elif verification_pending:
        reasons.append("Сверка карты и целостности обновляется; прошлые результаты устарели"
                       if verification_stale else "Сверка карты и целостности выполняется")
    if sources["failed"]:
        reasons.append(f'Ошибки у {sources["failed"]} настроенных источников')
    if sources["degraded"]:
        reasons.append(f'{sources["degraded"]} настроенных источников требуют внимания')
    if sources["paused_total"]:
        reasons.append(f'{sources["paused_total"]} настроенных источников на паузе')
    if sources["not_checked"]:
        reasons.append(f'{sources["not_checked"]} настроенных источников без проверки')
    if geo.get("paused"):
        reasons.append("Геокодирование приостановлено")
    if integrity["source_without_processed"] or integrity["processed_without_source"]:
        reasons.append("Есть несвязанные записи источников и лотов")
    if integrity["active_and_archived"] or integrity["date_invariants"]:
        reasons.append("Нарушены инварианты статусов или дат")
    if host.get("backup_healthy") is False:
        reasons.append("Резервная копия требует внимания")
    if host.get("runner_healthy") is False:
        reasons.append("Диагностика Runner устарела")
    if disk_status == "unknown":
        reasons.append("Нет свежих данных о свободном месте на диске")
    elif disk_status in {"warning", "critical"}:
        reasons.append("Недостаточно свободного места на диске")
    if critical:
        reasons.append("Нарушена целостность карты или набора данных")
    elif manifest_problem:
        reasons.append("Публичная доставка карты не подтверждена")
    elif manifest_unchecked:
        reasons.append("Публичная доставка карты не проверена")
    elif not map_report.get("ok"):
        reasons.append("Публикация карты не прошла сверку")

    incident = critical or manifest_problem or host.get("backup_healthy") is False or disk_status == "critical"
    urgent_reasons = []
    if critical:
        urgent_reasons.append("Нарушена целостность карты или набора данных")
    elif manifest_problem:
        urgent_reasons.append("Публичная доставка карты не подтверждена")
    if host.get("backup_healthy") is False:
        urgent_reasons.append("Резервная копия требует внимания")
    if disk_status == "critical":
        urgent_reasons.append("Недостаточно свободного места на диске")
    reasons = urgent_reasons + [reason for reason in reasons if reason not in urgent_reasons]
    degraded = bool(reasons or sources["failed"] or sources["degraded"] or sources["paused_total"] or sources["not_checked"])
    status = "incident" if incident else "degraded" if degraded else "ok"

    dataset = map_report.get("dataset") or {}
    delivery_ok = not verification_pending and bool(map_report.get("ok")) and manifest.get("checked") is True and manifest.get("ok") is True
    dataset_ok = (
        dataset.get("status") == "ready"
        and map_report.get("point_count_matches") is True
        and map_report.get("tile_count_matches") is True
        and integrity["current_dataset_count"] == 1
    )
    delivery_status = (
        "ok" if delivery_ok
        else "not_checked" if manifest_unchecked and not critical
        else "failed" if critical or manifest_problem
        else "degraded"
    )
    current_quality["current_population"] = current_quality.get("population")
    return {
        "schema_version": 1,
        "generated_at": sources["generated_at"],
        "system": {
            "status": status,
            "reasons": reasons[:8],
            "generated_at": sources["generated_at"],
            "deployment_sha": os.environ.get("BANKROTAI_DEPLOY_SHA", ""),
            "app_version": __version__,
            "schema_revision": SCHEMA_REVISION,
        },
        "sources": {key: value for key, value in sources.items() if key != "items"},
        "geo": geo,
        "map": {
            "checked_at": map_report.get("checked_at"),
            "ok": delivery_ok,
            "delivery_status": delivery_status,
            "db_ok": integrity["current_dataset_count"] == 1,
            "dataset_ok": dataset_ok,
            "s3_manifest_ok": manifest.get("ok") if manifest.get("checked") else None,
            "public_ok": manifest.get("ok") if manifest.get("checked") else None,
            "mismatch_counts": {
                "missing": int(map_report.get("missing_from_dataset_count") or 0),
                "extra": int(map_report.get("extra_in_dataset_count") or 0),
                "tile_count": integrity["tile_count_mismatches"],
            },
            "dataset_version": dataset.get("version"),
            "dataset_status": dataset.get("status"),
            "point_count": dataset.get("declared_point_count"),
            "published_at": dataset.get("published_at"),
            "point_count_matches": map_report.get("point_count_matches"),
            "tile_count_matches": map_report.get("tile_count_matches"),
            "missing_from_dataset_count": map_report.get("missing_from_dataset_count"),
            "extra_in_dataset_count": map_report.get("extra_in_dataset_count"),
            "pending_missing_count": map_report.get("pending_post_dataset_missing_count"),
            "pending_extra_count": map_report.get("pending_post_dataset_extra_count"),
            "public_manifest": map_report.get("manifest"),
        },
        "integrity": {
            **integrity,
            "checked": not verification_pending,
            "status": "error" if verification_error else "stale" if verification_stale else "not_checked" if verification_pending else "checked",
            "checked_at": operational.get("checked_at"),
            "critical_count": 1 if critical else 0,
            "warning_count": warning_count,
        },
        "host": host,
        "data_quality": current_quality,
    }


def prepare_reliability_snapshot(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))


def _refresh_summary_snapshot():
    from bankrotai.db import SessionLocal
    try:
        with SessionLocal() as session:
            prepare_reliability_snapshot(session)
            result = _reliability_summary_uncached(session)
        with _SUMMARY_CACHE_LOCK:
            _SUMMARY_CACHE.update({"at": time.monotonic(), "snapshot": deepcopy(result),
                                   "failed": False, "retry_at": 0.0})
    except Exception:
        with _SUMMARY_CACHE_LOCK:
            _SUMMARY_CACHE.update({"failed": True, "retry_at": time.monotonic() + 30.0})
    finally:
        with _SUMMARY_CACHE_LOCK:
            _SUMMARY_CACHE["running"] = False


def reliability_summary(session: Session) -> dict[str, Any]:
    """Serve the last consistent snapshot while one background refresh runs.

    Initial bootstrap reads the cheap live counters once. Subsequent cache
    expiry never blocks browser requests on aggregate SQL. A stale snapshot
    keeps its original timestamp and is explicitly marked, never silently green.
    """
    if not _summary_cache_enabled(session):
        return _reliability_summary_uncached(session)
    launch = False
    with _SUMMARY_CACHE_LOCK:
        now = time.monotonic()
        snapshot = _SUMMARY_CACHE.get("snapshot")
        if not isinstance(snapshot, dict):
            result = _reliability_summary_uncached(session)
            _SUMMARY_CACHE.update({"at": time.monotonic(), "snapshot": deepcopy(result), "failed": False})
            return {**result, "refresh_state": "fresh"}
        fresh = now - float(_SUMMARY_CACHE.get("at") or 0.0) < _SUMMARY_CACHE_TTL_SECONDS
        if not fresh and not _SUMMARY_CACHE.get("running") and now >= float(_SUMMARY_CACHE.get("retry_at") or 0):
            _SUMMARY_CACHE["running"] = launch = True
        result = deepcopy(snapshot)
        failed = bool(_SUMMARY_CACHE.get("failed"))
    result["refresh_state"] = "fresh" if fresh else "error" if failed else "refreshing"
    if not fresh:
        system = result["system"]
        if system.get("status") == "ok":
            system["status"] = "degraded"
        reason = ("Не удалось обновить состояние; показаны последние данные" if failed
                  else "Состояние обновляется; показаны последние подтверждённые данные")
        system["reasons"] = [reason, *system.get("reasons", [])][:8]
    if launch:
        try:
            threading.Thread(target=_refresh_summary_snapshot, daemon=True,
                             name="reliability-summary-snapshot").start()
        except RuntimeError:
            with _SUMMARY_CACHE_LOCK:
                _SUMMARY_CACHE.update({"running": False, "failed": True, "retry_at": now + 30.0})
    return result
