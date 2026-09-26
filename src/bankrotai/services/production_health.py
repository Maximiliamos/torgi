from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import BackgroundTaskState, LotSyncRun, MapDataset
from bankrotai.services.geo_backfill import geocoding_progress
from bankrotai.services.ingestion import default_source_specs
from bankrotai.services.quality import list_source_health


_GEO_STALL_AFTER = timedelta(hours=24)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _age_seconds(now: datetime, value: datetime | None) -> int | None:
    normalized = _utc_naive(value)
    normalized_now = _utc_naive(now)
    if normalized is None or normalized_now is None:
        return None
    return max(0, int((normalized_now - normalized).total_seconds()))


def build_phase3_health(
    session: Session,
    *,
    now: datetime | None = None,
    expected_sources: set[str] | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, *, severity: str = "critical", **details: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, **details})

    current_maps = session.scalars(
        select(MapDataset).where(MapDataset.is_current.is_(True))
    ).all()
    current = current_maps[0] if len(current_maps) == 1 else None
    add(
        "map-current-dataset",
        current is not None and current.status == "ready" and current.published_at is not None,
        current_count=len(current_maps),
        version=current.version if current is not None else None,
        status=current.status if current is not None else None,
        published_at=current.published_at if current is not None else None,
    )

    latest_failed_map = session.scalar(
        select(MapDataset)
        .where(MapDataset.status == "failed")
        .order_by(MapDataset.created_at.desc(), MapDataset.id.desc())
        .limit(1)
    )
    failed_created_at = _utc_naive(latest_failed_map.created_at) if latest_failed_map is not None else None
    current_published_at = _utc_naive(current.published_at) if current is not None else None
    failed_after_current = bool(
        failed_created_at is not None
        and current_published_at is not None
        and failed_created_at > current_published_at
    )
    add(
        "map-publication-last-attempt",
        not failed_after_current,
        failed_version=latest_failed_map.version if failed_after_current else None,
        failed_at=latest_failed_map.created_at if failed_after_current else None,
    )

    active_sync = session.scalar(
        select(LotSyncRun)
        .where(LotSyncRun.status.in_(("queued", "running")))
        .order_by(LotSyncRun.created_at.desc())
        .limit(1)
    )
    lease_expires_at = _utc_naive(active_sync.lease_expires_at) if active_sync is not None else None
    normalized_now = _utc_naive(now)
    lease_expired = bool(
        lease_expires_at is not None
        and normalized_now is not None
        and lease_expires_at < normalized_now
    )
    add(
        "source-sync-lease",
        not lease_expired,
        run_id=active_sync.id if active_sync is not None else None,
        status=active_sync.status if active_sync is not None else None,
        lease_expires_at=active_sync.lease_expires_at if active_sync is not None else None,
    )

    all_sources = list_source_health(session, now=now)
    configured_sources = expected_sources or {spec.source_id for spec in default_source_specs()}
    source_by_name = {source.source_system: source for source in all_sources}
    actual_sources = set(source_by_name)
    missing_sources = sorted(configured_sources - actual_sources)
    legacy_sources = sorted(actual_sources - configured_sources)
    sources = [
        source_by_name[name]
        for name in sorted(configured_sources)
        if name in source_by_name
    ]
    add(
        "source-health-present",
        bool(sources) and not missing_sources,
        source_count=len(sources),
        configured_source_count=len(configured_sources),
        missing_sources=missing_sources,
        legacy_source_count=len(legacy_sources),
        legacy_sources=legacy_sources,
    )
    for source in sources:
        degraded_upstream = source.last_error_category in {"coverage_guard", "access_limited"}
        if source.freshness_status == "delayed":
            freshness_ok = False
            freshness_severity = "warning"
        elif source.freshness_status in {"fresh", "running"}:
            freshness_ok = True
            freshness_severity = "critical"
        elif degraded_upstream:
            # A known upstream limitation is operationally degraded, but the
            # application itself is still healthy. Keep it visible without
            # turning the whole production health gate red forever.
            freshness_ok = False
            freshness_severity = "warning"
        else:
            freshness_ok = False
            freshness_severity = "critical"
        add(
            f"source-freshness:{source.source_system}",
            freshness_ok,
            severity=freshness_severity,
            status=source.status,
            freshness_status=source.freshness_status,
            freshness_age_seconds=source.freshness_age_seconds,
            last_success_at=source.last_success_at,
            last_error_category=source.last_error_category,
        )

        coverage_ok = source.coverage_status == "fresh"
        coverage_severity = (
            "warning"
            if (
                not coverage_ok
                and (
                    source.freshness_status in {"fresh", "running"}
                    or degraded_upstream
                )
            )
            else "critical"
        )
        add(
            f"source-coverage:{source.source_system}",
            coverage_ok,
            severity=coverage_severity,
            coverage_status=source.coverage_status,
            complete_snapshot_age_seconds=source.complete_snapshot_age_seconds,
            last_complete_success_at=source.last_complete_success_at,
            last_error_category=source.last_error_category,
        )

    geo = geocoding_progress(session)
    latest_geo = session.scalar(
        select(BackgroundTaskState)
        .where(
            BackgroundTaskState.task_type == "geocoding",
            BackgroundTaskState.status == "completed",
        )
        .order_by(BackgroundTaskState.finished_at.desc(), BackgroundTaskState.id.desc())
        .limit(1)
    )
    latest_geo_age = _age_seconds(now, latest_geo.finished_at if latest_geo is not None else None)
    actionable = int(geo.get("actionable_remaining") or 0)
    paused = bool(geo.get("paused"))
    geo_recent = latest_geo_age is not None and latest_geo_age <= int(_GEO_STALL_AFTER.total_seconds())
    add(
        "geo-backlog-liveness",
        actionable == 0 or paused or geo_recent,
        actionable_remaining=actionable,
        paused=paused,
        latest_completed_batch_at=latest_geo.finished_at if latest_geo is not None else None,
        latest_completed_batch_age_seconds=latest_geo_age,
        percent=geo.get("percent"),
    )

    critical_failures = [
        check for check in checks if not check["ok"] and check["severity"] == "critical"
    ]
    warnings = [
        check for check in checks if not check["ok"] and check["severity"] == "warning"
    ]
    checked_at = _utc_naive(now)
    if checked_at is None:
        raise RuntimeError("Production health clock is unavailable")
    return {
        "checked_at": checked_at.isoformat() + "Z",
        "healthy": not critical_failures,
        "critical_failure_count": len(critical_failures),
        "warning_count": len(warnings),
        "checks": checks,
        "summary": {
            "map_version": current.version if current is not None else None,
            "source_count": len(sources),
            "legacy_source_count": len(legacy_sources),
            "geo_percent": geo.get("percent"),
            "geo_actionable_remaining": actionable,
        },
    }
