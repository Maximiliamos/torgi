"""Read-only aggregate model for the Reliability dashboard."""
from __future__ import annotations

from datetime import datetime
import os
import threading
import time
from typing import Any
from urllib.parse import quote

import requests

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankrotai.core import get_settings
from bankrotai.db import AppSetting, ProcessedLot, utc_now
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
        return {"checked": True, "ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}


def _heavy_quality_bundle(session: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    now = time.monotonic()
    with _HEAVY_CACHE_LOCK:
        age = now - float(_HEAVY_CACHE.get("at") or 0.0)
        operational = _HEAVY_CACHE.get("operational")
        map_report = _HEAVY_CACHE.get("map")
        if age < _HEAVY_CACHE_TTL_SECONDS and isinstance(operational, dict) and isinstance(map_report, dict):
            return operational, map_report
        operational = operational_quality_report(session, stale_days=7, problem_limit=10)
        map_report = map_delivery_reconciliation_report(
            session,
            verify_public_manifest=False,
            problem_limit=10,
        )
        map_report["manifest"] = _quick_public_manifest(map_report.get("dataset"))
        _HEAVY_CACHE.update({"at": now, "operational": operational, "map": map_report})
        return operational, map_report


def _canonical_source(value: str) -> str:
    raw = (value or "").strip()
    return _SOURCE_ALIASES.get(raw.casefold(), raw.casefold())


def _current_error(item: Any) -> str | None:
    if not item.last_error or not item.last_failure_at:
        return None
    if item.last_success_at and item.last_success_at >= item.last_failure_at:
        return None
    return item.last_error


def _source_status(item: Any, *, paused: bool = False) -> str:
    if paused:
        return "paused"
    if item.status == "failed" or item.circuit_state in {"open", "half_open"} or _current_error(item):
        return "failed"
    if item.status == "partial" or item.freshness_status != "fresh" or item.coverage_status != "fresh":
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
        str(row.key).removeprefix("source_paused:")
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
            overall = _source_status(item, paused=source_id in paused)
            next_retry = item.next_retry_at
            retry_overdue = bool(next_retry is not None and next_retry < utc_now())
            row = {
                **item.model_dump(mode="json"),
                "canonical_id": source_id,
                "display_name_ru": _SOURCE_DISPLAY_RU.get(source_id, source_id),
                "aliases": sorted(aliases.get(source_id, set())),
                "configured": source_id in configured,
                "scheduled": source_id in configured,
                "registered": source_id in registered,
                "historical_only": source_id not in configured and source_id not in registered,
                "paused": source_id in paused,
                "overall_status": overall,
                "current_error": current_error,
                "last_historical_error": item.last_error,
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
        rows.append(row)

    order = {"failed": 0, "degraded": 1, "paused": 2, "not_checked": 3, "healthy": 4}
    rows.sort(key=lambda value: (order.get(str(value["overall_status"]), 9), str(value["display_name_ru"]).casefold()))
    return {
        "generated_at": utc_now().isoformat(),
        "configured_total": len(configured),
        "registered_total": len(registered),
        "monitored_total": len(rows),
        "paused_total": sum(bool(row["paused"]) for row in rows if row["configured"]),
        "healthy": sum(row["overall_status"] == "healthy" for row in rows if row["configured"]),
        "degraded": sum(row["overall_status"] == "degraded" for row in rows if row["configured"]),
        "failed": sum(row["overall_status"] == "failed" for row in rows if row["configured"]),
        "not_checked": sum(row["overall_status"] == "not_checked" for row in rows if row["configured"]),
        "items": rows,
    }


def _current_quality(session: Session) -> dict[str, Any]:
    live = (
        ProcessedLot.duplicate_of_id.is_(None)
        & ProcessedLot.is_archived.is_(False)
        & ProcessedLot.auction_status.notin_(tuple(ENDED_STATUSES))
    )

    def count(extra: Any | None = None) -> int:
        query = select(func.count()).select_from(ProcessedLot).where(live)
        if extra is not None:
            query = query.where(extra)
        return int(session.scalar(query) or 0)

    total = count()
    values: dict[str, Any] = {
        "population": total,
        "missing_address": count(ProcessedLot.address.is_(None) | (func.trim(ProcessedLot.address) == "")),
        "missing_cadastre": count(
            ProcessedLot.cadastral_number.is_(None) | (func.trim(ProcessedLot.cadastral_number) == "")
        ),
        "missing_price": count(ProcessedLot.current_price.is_(None) & ProcessedLot.start_price.is_(None)),
        "unknown_status": count(ProcessedLot.auction_status == "unknown"),
        "missing_region": count(
            ProcessedLot.region_code.is_(None) | (func.trim(ProcessedLot.region_code) == "")
        ),
        "missing_url": count(
            (ProcessedLot.lot_url.is_(None) | (func.trim(ProcessedLot.lot_url) == ""))
            & (ProcessedLot.source_url.is_(None) | (func.trim(ProcessedLot.source_url) == ""))
        ),
        "non_positive_price": count(
            (ProcessedLot.current_price.isnot(None) & (ProcessedLot.current_price <= 0))
            | (ProcessedLot.start_price.isnot(None) & (ProcessedLot.start_price <= 0))
        ),
    }
    values["percentages"] = {
        key: round((value / total * 100) if total else 0.0, 1)
        for key, value in values.items()
        if key not in {"population", "percentages"}
    }
    return values


def reliability_summary(session: Session) -> dict[str, Any]:
    sources = reliability_sources(session)
    geo = geocoding_progress(session)
    host = operations_host_status(session)
    operational, map_report = _heavy_quality_bundle(session)
    current_quality = _current_quality(session)

    operational_integrity = operational.get("integrity") or {}
    integrity = {
        "source_without_processed": int(operational_integrity.get("source_without_processed") or 0),
        "processed_without_source": int(operational_integrity.get("processed_without_source") or 0),
        "current_dataset_count": int(operational_integrity.get("current_dataset_count") or 0),
        "tile_count_mismatches": int(operational_integrity.get("tile_count_mismatches") or 0),
    }
    manifest = map_report.get("manifest") or {}
    manifest_problem = manifest.get("checked") is True and manifest.get("ok") is not True
    manifest_unchecked = manifest.get("checked") is not True
    critical = (
        integrity["current_dataset_count"] != 1
        or integrity["tile_count_mismatches"] > 0
        or bool(map_report.get("missing_from_dataset_count"))
        or bool(map_report.get("extra_in_dataset_count"))
        or (map_report.get("dataset") or {}).get("status") != "ready"
    )
    warning_count = sum(
        value > 0
        for key, value in integrity.items()
        if key not in {"current_dataset_count"}
    )

    reasons: list[str] = []
    if sources["failed"]:
        reasons.append(f'Ошибки у {sources["failed"]} настроенных источников')
    if sources["degraded"]:
        reasons.append(f'{sources["degraded"]} настроенных источников требуют внимания')
    if host.get("backup_healthy") is False:
        reasons.append("Резервная копия требует внимания")
    if host.get("runner_healthy") is False:
        reasons.append("Диагностика Runner устарела")
    disk_free = (host.get("disk") or {}).get("free_gb")
    if isinstance(disk_free, (int, float)) and disk_free < 25:
        reasons.append("Недостаточно свободного места на диске")
    if critical:
        reasons.append("Нарушена целостность карты или набора данных")
    elif manifest_problem:
        reasons.append("Публичная доставка карты не подтверждена")
    elif manifest_unchecked:
        reasons.append("Публичная доставка карты не проверена")

    incident = critical or host.get("backup_healthy") is False or (
        isinstance(disk_free, (int, float)) and disk_free < 15
    )
    degraded = bool(
        reasons
        or sources["failed"]
        or sources["degraded"]
        or geo.get("paused")
    )
    status = "incident" if incident else "degraded" if degraded else "ok"

    dataset = map_report.get("dataset") or {}
    delivery_ok = bool(map_report.get("ok")) and manifest.get("checked") is True and manifest.get("ok") is True
    return {
        "schema_version": 1,
        "generated_at": utc_now().isoformat(),
        "system": {
            "status": status,
            "reasons": reasons[:5],
            "deployment_sha": os.environ.get("BANKROTAI_DEPLOY_SHA", ""),
        },
        "sources": {key: value for key, value in sources.items() if key != "items"},
        "geo": geo,
        "map": {
            "ok": delivery_ok,
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
            "critical_count": 1 if critical else 0,
            "warning_count": warning_count,
        },
        "host": host,
        "data_quality": current_quality,
    }
