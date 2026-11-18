from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from bankrotai.connectors.registry import connector_registry
from bankrotai.db import (
    AppSetting,
    DiagnosticEvent,
    LotGeoSnapshot,
    MapDataset,
    ProcessedLot,
    SourceLot,
    utc_now,
)
from bankrotai.services.geo_backfill import geocoding_progress
from bankrotai.services.ingestion import default_source_specs
from bankrotai.services.map_payload import ENDED_STATUSES
from bankrotai.services.operations_status import operations_host_status
from bankrotai.services.quality import list_source_health, map_delivery_reconciliation_report


_SOURCE_ALIASES = {
    "tbankrot": "tbankrot.ru",
    "тбанкрот": "tbankrot.ru",
    "гис торги": "torgi.gov.ru",
    "гис-торги": "torgi.gov.ru",
    "рад / лот-онлайн": "lot-online.ru",
    "рад/лот-онлайн": "lot-online.ru",
    "лот-онлайн": "lot-online.ru",
}

_SOURCE_DISPLAY_NAMES = {
    "torgi.gov.ru": "ГИС Торги",
    "tbankrot.ru": "TBankrot",
    "lot-online.ru": "ЛОТ-ОНЛАЙН",
    "torgi-russia.ru": "Torgi Russia",
    "bidexpert.ru": "BidExpert",
    "m-ets.ru": "M-ETS",
    "cdtrf.ru": "ЦДТ",
    "nistp.ru": "НИСП",
    "sberbank-ast.ru": "Сбербанк-АСТ",
    "vertrades.ru": "ВЭТП",
    "roseltorg.ru": "Росэлторг",
    "rus-on.ru": "RUSSIA OnLine",
    "fabrikant.ru": "Фабрикант",
}

_STATUS_ORDER = {
    "failed": 0,
    "degraded": 1,
    "paused": 2,
    "not_checked": 3,
    "healthy": 4,
}

_MAP_DELIVERY_CACHE_TTL_SECONDS = 300
_map_delivery_cache_lock = threading.Lock()
_map_delivery_cache: dict[str, Any] = {
    "version": None,
    "expires_at": 0.0,
    "report": None,
}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _canonical_source_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    return _SOURCE_ALIASES.get(cleaned.casefold(), cleaned.casefold())


def _display_name(source_id: str) -> str:
    return _SOURCE_DISPLAY_NAMES.get(source_id, source_id)


def _setting_enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _latest_datetime(values: list[datetime | None]) -> datetime | None:
    available: list[datetime] = []
    for value in values:
        stamp = _naive_utc(value)
        if stamp is not None:
            available.append(stamp)
    return max(available) if available else None


def _row_sort_time(row: Any) -> datetime:
    return (
        _naive_utc(row.last_attempt_at)
        or _naive_utc(row.last_success_at)
        or _naive_utc(row.last_failure_at)
        or datetime.min
    )


def _serialize_datetime(value: datetime | None) -> datetime | None:
    return _naive_utc(value)


def _current_error_for_group(members: list[Any]) -> tuple[str | None, str | None, datetime | None, str | None, str | None]:
    latest_success_at = _latest_datetime([item.last_success_at for item in members])
    failed_members = [item for item in members if item.last_failure_at is not None]
    if not failed_members:
        return None, None, None, None, None
    failure = max(failed_members, key=lambda item: _naive_utc(item.last_failure_at) or datetime.min)
    failure_at = _naive_utc(failure.last_failure_at)
    historical_error = failure.last_error
    if failure_at is None or (latest_success_at is not None and failure_at <= latest_success_at):
        return None, None, failure_at, historical_error, failure.last_error_category
    return failure.last_error, failure.last_error_category, failure_at, historical_error, failure.last_error_category


def _database_record_counts(session: Session) -> dict[str, int]:
    values: dict[str, int] = defaultdict(int)
    for source_system, count in session.execute(
        select(ProcessedLot.source_system, func.count()).group_by(ProcessedLot.source_system)
    ):
        canonical = _canonical_source_id(source_system)
        if canonical:
            values[canonical] += int(count or 0)
    return dict(values)


def configured_source_ids() -> tuple[str, ...]:
    return tuple(sorted({_canonical_source_id(spec.source_id) for spec in default_source_specs()}))


def build_reliability_sources(session: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    current = _naive_utc(now or utc_now()) or datetime.utcnow()
    raw = list_source_health(session, now=current)
    groups: dict[str, list[Any]] = defaultdict(list)
    for item in raw:
        canonical = _canonical_source_id(item.source_system)
        if canonical:
            groups[canonical].append(item)

    configured = set(configured_source_ids())
    connected = {_canonical_source_id(value) for value in connector_registry.source_ids()}
    counts = _database_record_counts(session)

    paused_by_source: dict[str, bool] = {}
    for row in session.scalars(select(AppSetting).where(AppSetting.key.like("source_paused:%"))).all():
        source_id = _canonical_source_id(str(row.key).removeprefix("source_paused:"))
        if source_id:
            paused_by_source[source_id] = paused_by_source.get(source_id, False) or _setting_enabled(row.value)

    source_ids = sorted(set(groups) | configured | connected | set(counts) | set(paused_by_source))
    result: list[dict[str, Any]] = []

    for source_id in source_ids:
        members = groups.get(source_id, [])
        canonical_member = next((item for item in members if item.source_system.casefold() == source_id), None)
        primary = canonical_member or (max(members, key=_row_sort_time) if members else None)
        aliases = sorted({
            str(item.source_system)
            for item in members
            if str(item.source_system).strip().casefold() != source_id
        })
        paused = bool(paused_by_source.get(source_id, False))

        last_success_at = _latest_datetime([item.last_success_at for item in members])
        last_failure_at = _latest_datetime([item.last_failure_at for item in members])
        last_attempt_at = _latest_datetime([item.last_attempt_at for item in members])
        last_complete_success_at = _latest_datetime([item.last_complete_success_at for item in members])
        current_error, current_error_category, _, historical_error, historical_error_category = _current_error_for_group(members)

        status = str(getattr(primary, "status", "not_checked") or "not_checked")
        freshness = str(getattr(primary, "freshness_status", "unknown") or "unknown")
        coverage = str(getattr(primary, "coverage_status", "unknown") or "unknown")
        circuit = str(getattr(primary, "circuit_state", "closed") or "closed")
        next_retry_at = _naive_utc(getattr(primary, "next_retry_at", None))
        retry_overdue = bool(
            next_retry_at is not None
            and next_retry_at < current
            and (current_error is not None or status == "failed" or circuit in {"open", "half_open"})
        )

        if paused:
            overall_status = "paused"
        elif current_error is not None or status == "failed" or circuit in {"open", "half_open"}:
            overall_status = "failed"
        elif primary is None and int(counts.get(source_id, 0)) == 0:
            overall_status = "not_checked"
        elif status != "healthy" or freshness != "fresh" or coverage != "fresh" or retry_overdue:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        role = (
            "configured"
            if source_id in configured
            else "pilot"
            if source_id in connected
            else "historical_only"
        )

        result.append({
            "canonical_id": source_id,
            "display_name_ru": _display_name(source_id),
            "aliases": aliases,
            "role": role,
            "configured": source_id in configured,
            "scheduled": source_id in configured,
            "paused": paused,
            "overall_status": overall_status,
            "sync_status": status,
            "freshness_status": freshness,
            "coverage_status": coverage,
            "circuit_state": circuit,
            "items_seen": int(getattr(primary, "items_seen", 0) or 0),
            "database_records": int(counts.get(source_id, 0)),
            "last_attempt_at": _serialize_datetime(last_attempt_at),
            "last_success_at": _serialize_datetime(last_success_at),
            "last_complete_success_at": _serialize_datetime(last_complete_success_at),
            "last_failure_at": _serialize_datetime(last_failure_at),
            "freshness_age_seconds": getattr(primary, "freshness_age_seconds", None),
            "complete_snapshot_age_seconds": getattr(primary, "complete_snapshot_age_seconds", None),
            "current_error": current_error,
            "current_error_category": current_error_category,
            "last_historical_error": historical_error,
            "last_historical_error_category": historical_error_category,
            "next_retry_at": _serialize_datetime(next_retry_at),
            "retry_overdue": retry_overdue,
            "retryable": bool(getattr(primary, "retryable", False)),
            "operational_failure": bool(getattr(primary, "operational_failure", False)),
            "last_probe_at": _serialize_datetime(getattr(primary, "last_probe_at", None)),
            "last_probe_success_at": _serialize_datetime(getattr(primary, "last_probe_success_at", None)),
            "last_pages_scanned": int(getattr(primary, "last_pages_scanned", 0) or 0),
            "last_items_inserted": int(getattr(primary, "last_items_inserted", 0) or 0),
            "last_items_updated": int(getattr(primary, "last_items_updated", 0) or 0),
            "last_items_unchanged": int(getattr(primary, "last_items_unchanged", 0) or 0),
            "last_items_archived": int(getattr(primary, "last_items_archived", 0) or 0),
            "last_items_failed": int(getattr(primary, "last_items_failed", 0) or 0),
            "network_fingerprint": dict(getattr(primary, "network_fingerprint", None) or {}),
        })

    return sorted(
        result,
        key=lambda item: (
            0 if item["configured"] else 1 if item["role"] == "pilot" else 2,
            _STATUS_ORDER.get(str(item["overall_status"]), 9),
            str(item["display_name_ru"]).casefold(),
        ),
    )


def _current_data_quality(session: Session) -> dict[str, Any]:
    ended = tuple(str(value).casefold() for value in ENDED_STATUSES)
    current = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        func.lower(func.coalesce(ProcessedLot.auction_status, "unknown")).not_in(ended),
    )

    def count(*conditions: Any) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(ProcessedLot).where(*current, *conditions)
            )
            or 0
        )

    population = count()
    missing_address = count(
        or_(ProcessedLot.address.is_(None), func.trim(ProcessedLot.address) == "")
    )
    missing_cadastre = count(
        or_(
            ProcessedLot.cadastral_number.is_(None),
            func.trim(ProcessedLot.cadastral_number) == "",
        )
    )
    missing_price = count(
        ProcessedLot.current_price.is_(None),
        ProcessedLot.start_price.is_(None),
    )
    unknown_status = count(
        func.lower(func.coalesce(ProcessedLot.auction_status, "unknown")) == "unknown"
    )
    missing_region = count(
        or_(ProcessedLot.region_code.is_(None), func.trim(ProcessedLot.region_code) == "")
    )
    missing_url = count(
        and_(
            or_(ProcessedLot.lot_url.is_(None), func.trim(ProcessedLot.lot_url) == ""),
            or_(ProcessedLot.source_url.is_(None), func.trim(ProcessedLot.source_url) == ""),
        )
    )
    non_positive_price = count(
        or_(
            and_(ProcessedLot.current_price.is_not(None), ProcessedLot.current_price <= 0),
            and_(ProcessedLot.start_price.is_not(None), ProcessedLot.start_price <= 0),
        )
    )

    def metric(value: int) -> dict[str, Any]:
        return {
            "count": value,
            "percent": round((value / population * 100) if population else 0.0, 1),
        }

    return {
        "current_population": population,
        "scope_ru": "Актуальные неархивные основные публикации, не завершённые по статусу",
        "missing_address": metric(missing_address),
        "missing_cadastre": metric(missing_cadastre),
        "missing_price": metric(missing_price),
        "unknown_status": metric(unknown_status),
        "missing_region": metric(missing_region),
        "missing_url": metric(missing_url),
        "non_positive_price": metric(non_positive_price),
        "historical_total": int(session.scalar(select(func.count()).select_from(ProcessedLot)) or 0),
    }


def _integrity_snapshot(session: Session, map_report: dict[str, Any]) -> dict[str, Any]:
    source_without_processed = int(
        session.scalar(
            select(func.count()).select_from(SourceLot).where(SourceLot.processed_lot_id.is_(None))
        )
        or 0
    )
    processed_without_source = int(
        session.scalar(
            select(func.count()).select_from(ProcessedLot).where(
                ~select(SourceLot.id).where(SourceLot.processed_lot_id == ProcessedLot.id).exists()
            )
        )
        or 0
    )
    active_and_archived = int(
        session.scalar(
            select(func.count()).select_from(SourceLot).where(
                SourceLot.is_active.is_(True),
                SourceLot.is_archived.is_(True),
            )
        )
        or 0
    )
    archived_without_timestamp = int(
        session.scalar(
            select(func.count()).select_from(ProcessedLot).where(
                ProcessedLot.is_archived.is_(True),
                ProcessedLot.archived_at.is_(None),
            )
        )
        or 0
    )
    first_seen_after_last_seen = int(
        session.scalar(
            select(func.count()).select_from(SourceLot).where(
                SourceLot.first_seen_at > SourceLot.last_seen_at
            )
        )
        or 0
    )
    application_start_after_deadline = int(
        session.scalar(
            select(func.count()).select_from(SourceLot).where(
                SourceLot.application_start_at.is_not(None),
                SourceLot.application_deadline.is_not(None),
                SourceLot.application_start_at > SourceLot.application_deadline,
            )
        )
        or 0
    )
    archived_before_first_seen = int(
        session.scalar(
            select(func.count()).select_from(SourceLot).where(
                SourceLot.archived_at.is_not(None),
                SourceLot.archived_at < SourceLot.first_seen_at,
            )
        )
        or 0
    )

    map_dataset_count_bad = int(map_report.get("current_dataset_count") or 0) != 1
    map_tile_mismatch = not bool(map_report.get("tile_count_matches", False))
    map_point_mismatch = not bool(map_report.get("point_count_matches", False))
    map_missing = int(map_report.get("missing_from_dataset_count") or 0)
    map_extra = int(map_report.get("extra_in_dataset_count") or 0)

    critical_checks = {
        "active_and_archived_source_lots": active_and_archived,
        "map_dataset_count": 1 if map_dataset_count_bad else 0,
        "map_tile_count_mismatch": 1 if map_tile_mismatch else 0,
        "map_point_count_mismatch": 1 if map_point_mismatch else 0,
        "map_missing_from_dataset": map_missing,
        "map_extra_in_dataset": map_extra,
    }
    warning_checks = {
        "source_without_processed": source_without_processed,
        "processed_without_source": processed_without_source,
        "archived_processed_without_timestamp": archived_without_timestamp,
        "first_seen_after_last_seen": first_seen_after_last_seen,
        "application_start_after_deadline": application_start_after_deadline,
        "archived_before_first_seen": archived_before_first_seen,
    }
    return {
        "critical_count": sum(1 for value in critical_checks.values() if value > 0),
        "warning_count": sum(1 for value in warning_checks.values() if value > 0),
        **critical_checks,
        **warning_checks,
    }


def _cached_map_delivery(session: Session, *, verify_manifest: bool = True) -> dict[str, Any]:
    current = session.scalar(
        select(MapDataset)
        .where(MapDataset.is_current.is_(True))
        .order_by(MapDataset.id.desc())
        .limit(1)
    )
    version = current.version if current is not None else "__none__"
    now = time.monotonic()
    if verify_manifest:
        with _map_delivery_cache_lock:
            if (
                _map_delivery_cache.get("version") == version
                and float(_map_delivery_cache.get("expires_at") or 0.0) > now
                and isinstance(_map_delivery_cache.get("report"), dict)
            ):
                return dict(_map_delivery_cache["report"])

    report = map_delivery_reconciliation_report(
        session,
        verify_public_manifest=verify_manifest,
        problem_limit=20,
    )
    if verify_manifest:
        with _map_delivery_cache_lock:
            _map_delivery_cache.update({
                "version": version,
                "expires_at": now + _MAP_DELIVERY_CACHE_TTL_SECONDS,
                "report": dict(report),
            })
    return report


def reliability_map_delivery(session: Session, *, verify_manifest: bool = True) -> dict[str, Any]:
    report = _cached_map_delivery(session, verify_manifest=verify_manifest)
    dataset = report.get("dataset") or {}
    manifest = report.get("manifest") or {}
    manifest_checked = manifest.get("checked") is True
    manifest_ok = manifest.get("ok") is True

    if report.get("ok") is True and manifest_checked and manifest_ok:
        status = "ok"
    elif (
        int(report.get("current_dataset_count") or 0) != 1
        or report.get("point_count_matches") is False
        or report.get("tile_count_matches") is False
        or int(report.get("missing_from_dataset_count") or 0) > 0
        or int(report.get("extra_in_dataset_count") or 0) > 0
        or (manifest_checked and not manifest_ok)
    ):
        status = "incident"
    else:
        status = "degraded"

    return {
        "status": status,
        "dataset_version": dataset.get("version"),
        "point_count": dataset.get("declared_point_count"),
        "tile_count": dataset.get("declared_tile_count"),
        "published_at": dataset.get("published_at"),
        "db_ok": int(report.get("current_dataset_count") or 0) == 1,
        "dataset_ok": bool(
            report.get("point_count_matches")
            and report.get("tile_count_matches")
            and int(report.get("missing_from_dataset_count") or 0) == 0
            and int(report.get("extra_in_dataset_count") or 0) == 0
        ),
        "s3_manifest_checked": manifest_checked,
        "s3_manifest_ok": manifest.get("ok"),
        "public_ok": manifest.get("ok"),
        "db_eligible_count": int(report.get("db_eligible_count") or 0),
        "dataset_unique_lot_count": int(report.get("dataset_unique_lot_count") or 0),
        "missing_from_dataset_count": int(report.get("missing_from_dataset_count") or 0),
        "extra_in_dataset_count": int(report.get("extra_in_dataset_count") or 0),
        "pending_post_dataset_missing_count": int(report.get("pending_post_dataset_missing_count") or 0),
        "pending_post_dataset_extra_count": int(report.get("pending_post_dataset_extra_count") or 0),
        "tile_count_matches": report.get("tile_count_matches"),
        "point_count_matches": report.get("point_count_matches"),
        "manifest_error": manifest.get("error"),
        "raw": report,
    }


def _journal_category(component: str) -> str:
    value = str(component or "").casefold()
    if any(marker in value for marker in ("source", "ingest", "etp", "sync")):
        return "sources"
    if "geo" in value:
        return "geo"
    if "map" in value or "s3" in value:
        return "map"
    if any(marker in value for marker in ("api", "runner", "backup", "maintenance", "docker", "host")):
        return "infrastructure"
    return "system"


def _reliability_journal(session: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(DiagnosticEvent)
        .order_by(desc(DiagnosticEvent.created_at), desc(DiagnosticEvent.id))
        .limit(max(1, min(limit, 100)))
    ).all()
    return [
        {
            "id": int(row.id),
            "category": _journal_category(row.component),
            "severity": str(row.severity or "info"),
            "title": str(row.component or "Система"),
            "detail": str(row.message or ""),
            "at": row.created_at,
        }
        for row in rows
    ]


def _disk_status(host: dict[str, Any]) -> dict[str, Any]:
    disk = dict(host.get("disk") or {})
    free = disk.get("free_gb")
    if not isinstance(free, (int, float)):
        status = "unknown"
    elif float(free) < 15:
        status = "critical"
    elif float(free) < 25:
        status = "warning"
    else:
        status = "ok"
    disk["status"] = status
    return disk


def build_reliability_summary(
    session: Session,
    *,
    deployment_sha: str = "",
    app_version: str = "",
    schema_revision: str = "",
    api_read_only: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _naive_utc(now or utc_now()) or datetime.utcnow()
    source_rows = build_reliability_sources(session, now=generated_at)
    configured_rows = [item for item in source_rows if item["configured"]]
    source_counts: dict[str, Any] = {
        "configured_total": len(configured_rows),
        "scheduled_total": sum(1 for item in configured_rows if item["scheduled"]),
        "connected_total": sum(1 for item in source_rows if item["role"] in {"configured", "pilot"}),
        "pilot_total": sum(1 for item in source_rows if item["role"] == "pilot"),
        "historical_total": sum(1 for item in source_rows if item["role"] == "historical_only"),
        "paused_total": sum(1 for item in configured_rows if item["paused"]),
        "healthy": sum(1 for item in configured_rows if item["overall_status"] == "healthy"),
        "degraded": sum(1 for item in configured_rows if item["overall_status"] == "degraded"),
        "failed": sum(1 for item in configured_rows if item["overall_status"] == "failed"),
        "not_checked": sum(1 for item in configured_rows if item["overall_status"] == "not_checked"),
        "complete_fresh": sum(1 for item in configured_rows if item["coverage_status"] == "fresh"),
        "stale": sum(1 for item in configured_rows if item["freshness_status"] == "stale"),
    }
    complete_ages = [
        int(item["complete_snapshot_age_seconds"])
        for item in configured_rows
        if isinstance(item.get("complete_snapshot_age_seconds"), int)
    ]
    source_counts["oldest_complete_snapshot_age_seconds"] = max(complete_ages) if complete_ages else None

    from bankrotai.services.source_resilience import source_global_network_state

    source_counts["network"] = source_global_network_state(session, now=generated_at)

    geo = dict(geocoding_progress(session))
    geo["population"] = int(geo.get("total") or 0)
    geo["current_geocoded"] = int(geo.get("geocoded") or 0)
    geo["history_lots"] = int(
        session.scalar(select(func.count(func.distinct(LotGeoSnapshot.lot_id)))) or 0
    )
    geo["scope_ru"] = "Актуальные неархивные основные публикации, где есть адрес или кадастровый номер"

    map_snapshot = reliability_map_delivery(session, verify_manifest=True)
    integrity = _integrity_snapshot(session, map_snapshot["raw"])
    data_quality = _current_data_quality(session)

    host_raw = operations_host_status(session, now=generated_at)
    disk = _disk_status(host_raw)
    backup = dict(host_raw.get("backup") or {})
    backup.update({
        "healthy": bool(host_raw.get("backup_healthy")),
        "age_hours": host_raw.get("backup_age_hours"),
        "threshold_hours": 60,
    })
    runner = dict(host_raw.get("runner") or {})
    runner.update({
        "fresh": bool(host_raw.get("runner_healthy")),
        "age_seconds": host_raw.get("runner_age_seconds"),
        "meaning_ru": "Возраст последней сохранённой диагностики runner, а не доказательство online/offline",
    })
    host = {
        "api": {"status": "ok"},
        "database": {"status": "ok"},
        "queue": {
            "status": "disabled_read_only" if api_read_only else "configured",
            "label_ru": "Отключена в read-only режиме" if api_read_only else "Настроена",
        },
        "disk": disk,
        "backup": backup,
        "runner_diagnostic": runner,
    }

    reasons: list[dict[str, str]] = []
    incident = False

    if map_snapshot["status"] == "incident":
        incident = True
        reasons.append({
            "severity": "critical",
            "code": "map_delivery",
            "title": "Нарушена целостность доставки карты",
            "detail": "База, MapDataset, тайлы или публичный manifest не согласованы.",
        })
    if integrity["critical_count"] > 0:
        incident = True
        reasons.append({
            "severity": "critical",
            "code": "database_integrity",
            "title": "Есть критические нарушения целостности",
            "detail": f"Критических классов: {integrity['critical_count']}.",
        })
    if not backup["healthy"]:
        incident = True
        reasons.append({
            "severity": "critical",
            "code": "backup",
            "title": "Резервная копия требует внимания",
            "detail": "Нет свежего backup с подтверждённым восстановлением в допустимом окне.",
        })
    if disk["status"] == "critical":
        incident = True
        reasons.append({
            "severity": "critical",
            "code": "disk",
            "title": "Критически мало места на диске",
            "detail": "Свободно менее 15 ГБ.",
        })

    if source_counts["failed"] > 0 or source_counts["degraded"] > 0 or source_counts["not_checked"] > 0:
        reasons.append({
            "severity": "warning",
            "code": "sources",
            "title": "Часть источников требует внимания",
            "detail": (
                f"Ошибки: {source_counts['failed']} · "
                f"ограничения: {source_counts['degraded']} · "
                f"не проверены: {source_counts['not_checked']}."
            ),
        })
    if source_counts["configured_total"] and source_counts["complete_fresh"] < source_counts["configured_total"]:
        reasons.append({
            "severity": "warning",
            "code": "coverage",
            "title": "Не все полные снимки источников актуальны",
            "detail": f"Актуальны {source_counts['complete_fresh']} из {source_counts['configured_total']}.",
        })
    if bool(geo.get("paused")):
        reasons.append({
            "severity": "warning",
            "code": "geo_paused",
            "title": "GEO поставлен на паузу",
            "detail": f"Требуют обработки или повторной проверки: {int(geo.get('actionable_remaining') or 0):,}.",
        })
    elif int(geo.get("actionable_remaining") or 0) > 0:
        reasons.append({
            "severity": "warning",
            "code": "geo_tail",
            "title": "Есть очередь GEO",
            "detail": f"Требуют обработки или повторной проверки: {int(geo.get('actionable_remaining') or 0):,}.",
        })
    if not runner["fresh"]:
        reasons.append({
            "severity": "warning",
            "code": "runner_diagnostic",
            "title": "Диагностика runner устарела",
            "detail": "Это не означает, что runner offline; требуется свежая диагностическая проверка.",
        })
    if disk["status"] == "warning":
        reasons.append({
            "severity": "warning",
            "code": "disk_warning",
            "title": "Запас диска ниже рекомендуемого",
            "detail": "Свободно от 15 до 25 ГБ.",
        })
    if map_snapshot["status"] == "degraded":
        reasons.append({
            "severity": "warning",
            "code": "map_unverified",
            "title": "Карта не полностью подтверждена",
            "detail": "Требуется завершить сверку DB → Dataset → S3 → Public.",
        })

    if incident:
        system_status = "incident"
        title_ru = "Требуется вмешательство"
    elif reasons:
        system_status = "degraded"
        title_ru = "Работает с ограничениями"
    else:
        system_status = "ok"
        title_ru = "Система работает нормально"

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "system": {
            "status": system_status,
            "title_ru": title_ru,
            "reasons": reasons,
            "deployment_sha": deployment_sha,
            "app_version": app_version,
            "schema_revision": schema_revision,
        },
        "sources": source_counts,
        "geo": geo,
        "map": {key: value for key, value in map_snapshot.items() if key != "raw"},
        "integrity": integrity,
        "host": host,
        "data_quality": data_quality,
        "journal": _reliability_journal(session),
    }
