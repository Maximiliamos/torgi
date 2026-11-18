"""Read-only aggregate model for the Reliability dashboard."""
from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    from bankrotai.services.operations import paused_source_systems

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
    values = {
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
    operational = operational_quality_report(session, stale_days=7, problem_limit=10)
    map_report = map_delivery_reconciliation_report(
        session,
        verify_public_manifest=False,
        problem_limit=10,
    )
    current_quality = _current_quality(session)

    integrity = {
        "source_without_processed": int(operational.get("source_without_processed") or 0),
        "processed_without_source": int(operational.get("processed_without_source") or 0),
        "current_dataset_count": int(operational.get("current_dataset_count") or 0),
        "tile_count_mismatches": int(operational.get("tile_count_mismatches") or 0),
    }
    critical = (
        integrity["current_dataset_count"] != 1
        or integrity["tile_count_mismatches"] > 0
        or bool(map_report.get("missing_from_dataset_count"))
        or bool(map_report.get("extra_in_dataset_count"))
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
            "ok": bool(map_report.get("ok")),
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
