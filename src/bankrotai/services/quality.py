from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import (
    DiagnosticEvent,
    GeoFailure,
    LotDocumentVersion,
    LotGeoSnapshot,
    LotPriceEvent,
    LotSyncRun,
    LotSyncSourceRun,
    MapDataset,
    MapTile,
    ProcessedLot,
    RawLot,
    SourceHealthState,
    SourceLot,
)
from bankrotai.dto import DataQualityDTO, SourceHealthDTO
from bankrotai.services.source_resilience import source_resilience_status


def data_quality_snapshot(session: Session) -> DataQualityDTO:
    scalar = session.scalar
    return DataQualityDTO(
        total_lots=scalar(select(func.count()).select_from(ProcessedLot)) or 0,
        active_lots=scalar(select(func.count()).where(ProcessedLot.auction_status == "active")) or 0,
        archived_lots=scalar(select(func.count()).where(ProcessedLot.is_archived.is_(True))) or 0,
        duplicate_lots=scalar(select(func.count()).where(ProcessedLot.duplicate_of_id.isnot(None))) or 0,
        missing_address=scalar(
            select(func.count()).where((ProcessedLot.address.is_(None)) | (func.trim(ProcessedLot.address) == ""))
        )
        or 0,
        missing_cadastre=scalar(
            select(func.count()).where(
                (ProcessedLot.cadastral_number.is_(None)) | (func.trim(ProcessedLot.cadastral_number) == "")
            )
        )
        or 0,
        missing_price=scalar(
            select(func.count()).where(ProcessedLot.current_price.is_(None), ProcessedLot.start_price.is_(None))
        )
        or 0,
        geocoded_lots=scalar(select(func.count(func.distinct(LotGeoSnapshot.lot_id)))) or 0,
        geo_attention_lots=scalar(select(func.count()).where(ProcessedLot.needs_geo_check.is_(True))) or 0,
        queued_geo_failures=scalar(
            select(func.count()).where(GeoFailure.status.not_in(("resolved", "terminal")))
        )
        or 0,
        unknown_status_lots=scalar(select(func.count()).where(ProcessedLot.auction_status == "unknown")) or 0,
        ai_analyzed_lots=scalar(
            select(func.count()).where(
                (ProcessedLot.market_price.isnot(None)) | (ProcessedLot.ai_recommendation.isnot(None))
            )
        )
        or 0,
        document_versions=scalar(select(func.count()).select_from(LotDocumentVersion)) or 0,
    )


def operational_quality_report(session: Session, *, stale_days: int = 7, problem_limit: int = 100) -> dict[str, Any]:
    """Repeatable source/data/map health report with actionable lot identifiers."""
    from bankrotai.services.map_view import extract_map_image_urls

    now = utc_now()
    cutoff = now.replace(tzinfo=None) - timedelta(days=max(1, stale_days))
    source_rows = session.execute(
        select(
            SourceLot.source_system,
            SourceLot.is_active,
            SourceLot.is_archived,
            SourceLot.source_status,
            SourceLot.last_seen_at,
            SourceLot.raw_data,
        )
    ).yield_per(1000)
    sources: dict[str, dict[str, Any]] = {}
    for source_system, is_active, is_archived, status, last_seen_at, raw_data in source_rows:
        value = sources.setdefault(source_system, {
            "total": 0, "active": 0, "archived": 0, "with_photos": 0,
            "unknown_status": 0, "stale_active": 0, "last_seen_at": None,
        })
        value["total"] += 1
        value["active"] += int(bool(is_active and not is_archived))
        value["archived"] += int(bool(is_archived))
        value["with_photos"] += int(bool(extract_map_image_urls(raw_data)))
        value["unknown_status"] += int(not status or status == "unknown")
        value["stale_active"] += int(bool(is_active and not is_archived and last_seen_at < cutoff))
        if last_seen_at and (value["last_seen_at"] is None or last_seen_at > value["last_seen_at"]):
            value["last_seen_at"] = last_seen_at

    problem_query = (
        select(SourceLot.id, SourceLot.source_system, SourceLot.external_id, SourceLot.last_seen_at)
        .where(
            SourceLot.is_active.is_(True),
            SourceLot.is_archived.is_(False),
            SourceLot.last_seen_at < cutoff,
        )
        .order_by(SourceLot.last_seen_at, SourceLot.id)
        .limit(max(1, min(problem_limit, 1000)))
    )
    stale_lots = [
        {"source_lot_id": row.id, "source_system": row.source_system, "external_id": row.external_id,
         "last_seen_at": row.last_seen_at}
        for row in session.execute(problem_query)
    ]
    current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
    last_run = session.scalar(select(LotSyncRun).order_by(LotSyncRun.started_at.desc()).limit(1))
    source_without_processed = session.scalar(
        select(func.count()).select_from(SourceLot).where(SourceLot.processed_lot_id.is_(None))
    ) or 0
    recoverable_source_links = session.scalar(
        select(func.count()).select_from(SourceLot).join(
            ProcessedLot,
            (ProcessedLot.source_system == SourceLot.source_system)
            & (ProcessedLot.external_id == SourceLot.external_id),
        ).where(SourceLot.processed_lot_id.is_(None))
    ) or 0
    processed_without_source = session.scalar(
        select(func.count()).select_from(ProcessedLot).where(
            ~select(SourceLot.id).where(SourceLot.processed_lot_id == ProcessedLot.id).exists()
        )
    ) or 0
    current_dataset_count = session.scalar(
        select(func.count()).select_from(MapDataset).where(MapDataset.is_current.is_(True))
    ) or 0
    tile_count_mismatches = session.scalar(
        select(func.count()).select_from(MapDataset).where(
            MapDataset.tile_count
            != select(func.count()).select_from(MapTile).where(MapTile.dataset_id == MapDataset.id).scalar_subquery()
        )
    ) or 0

    public_lot = (
        ProcessedLot.duplicate_of_id.is_(None)
        & ProcessedLot.is_archived.is_(False)
    )
    active_duplicate_lot = (
        ProcessedLot.duplicate_of_id.isnot(None)
        & ProcessedLot.is_archived.is_(False)
    )
    missing_title = public_lot & (func.trim(ProcessedLot.title) == "")
    missing_region = public_lot & (
        ProcessedLot.region_code.is_(None) | (func.trim(ProcessedLot.region_code) == "")
    )
    missing_url = public_lot & (
        (ProcessedLot.lot_url.is_(None) | (func.trim(ProcessedLot.lot_url) == ""))
        & (ProcessedLot.source_url.is_(None) | (func.trim(ProcessedLot.source_url) == ""))
    )
    missing_price = public_lot & (
        ProcessedLot.current_price.is_(None) & ProcessedLot.start_price.is_(None)
    )
    non_positive_price = public_lot & (
        (ProcessedLot.current_price.isnot(None) & (ProcessedLot.current_price <= 0))
        | (ProcessedLot.start_price.isnot(None) & (ProcessedLot.start_price <= 0))
    )
    unknown_status = public_lot & (ProcessedLot.auction_status == "unknown")
    has_current_geo = public_lot & (
        ProcessedLot.current_geo_lat.isnot(None) & ProcessedLot.current_geo_lon.isnot(None)
    )

    first_seen_after_last_seen = SourceLot.first_seen_at > SourceLot.last_seen_at
    application_start_after_deadline = (
        SourceLot.application_start_at.isnot(None)
        & SourceLot.application_deadline.isnot(None)
        & (SourceLot.application_start_at > SourceLot.application_deadline)
    )
    archived_before_first_seen = (
        SourceLot.archived_at.isnot(None)
        & (SourceLot.archived_at < SourceLot.first_seen_at)
    )

    sample_limit = max(1, min(problem_limit, 1000))

    def processed_count(condition: Any) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(ProcessedLot).where(condition)
            )
            or 0
        )

    def source_count(condition: Any) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(SourceLot).where(condition)
            )
            or 0
        )

    def processed_sample(condition: Any) -> list[int]:
        return list(
            session.scalars(
                select(ProcessedLot.id)
                .where(condition)
                .order_by(ProcessedLot.id)
                .limit(sample_limit)
            ).all()
        )

    def source_sample(condition: Any) -> list[int]:
        return list(
            session.scalars(
                select(SourceLot.id)
                .where(condition)
                .order_by(SourceLot.id)
                .limit(sample_limit)
            ).all()
        )

    lot_data_quality = {
        "active_non_duplicate_lots": processed_count(public_lot),
        "active_duplicate_lots": processed_count(active_duplicate_lot),
        "missing_title": processed_count(missing_title),
        "missing_region": processed_count(missing_region),
        "missing_url": processed_count(missing_url),
        "missing_price": processed_count(missing_price),
        "non_positive_price": processed_count(non_positive_price),
        "unknown_status": processed_count(unknown_status),
        "problem_samples": {
            "missing_title_lot_ids": processed_sample(missing_title),
            "missing_region_lot_ids": processed_sample(missing_region),
            "missing_url_lot_ids": processed_sample(missing_url),
            "missing_price_lot_ids": processed_sample(missing_price),
            "non_positive_price_lot_ids": processed_sample(non_positive_price),
            "unknown_status_lot_ids": processed_sample(unknown_status),
        },
    }
    source_date_quality = {
        "first_seen_after_last_seen": source_count(first_seen_after_last_seen),
        "application_start_after_deadline": source_count(application_start_after_deadline),
        "archived_before_first_seen": source_count(archived_before_first_seen),
        "problem_samples": {
            "first_seen_after_last_seen_source_lot_ids": source_sample(first_seen_after_last_seen),
            "application_start_after_deadline_source_lot_ids": source_sample(application_start_after_deadline),
            "archived_before_first_seen_source_lot_ids": source_sample(archived_before_first_seen),
        },
    }
    return {
        "generated_at": now,
        "stale_after_days": max(1, stale_days),
        "sources": sources,
        "geocoding": {
            "with_coordinates": processed_count(has_current_geo),
            "needs_attention": session.scalar(
                select(func.count()).where(ProcessedLot.needs_geo_check.is_(True))
            ) or 0,
            "queued_failures": session.scalar(
                select(func.count()).where(GeoFailure.status.not_in(("resolved", "terminal")))
            ) or 0,
        },
        "price_changes_24h": session.scalar(
            select(func.count()).where(LotPriceEvent.observed_at >= now.replace(tzinfo=None) - timedelta(hours=24))
        ) or 0,
        "unknown_status_lots": session.scalar(
            select(func.count()).where(ProcessedLot.auction_status == "unknown")
        ) or 0,
        "lot_data_quality": lot_data_quality,
        "source_date_quality": source_date_quality,
        "integrity": {
            "source_without_processed": source_without_processed,
            "recoverable_source_links": recoverable_source_links,
            "processed_without_source": processed_without_source,
            "active_and_archived_source_lots": session.scalar(
                select(func.count()).select_from(SourceLot).where(
                    SourceLot.is_active.is_(True), SourceLot.is_archived.is_(True)
                )
            ) or 0,
            "archived_processed_without_timestamp": session.scalar(
                select(func.count()).select_from(ProcessedLot).where(
                    ProcessedLot.is_archived.is_(True), ProcessedLot.archived_at.is_(None)
                )
            ) or 0,
            "current_dataset_count": current_dataset_count,
            "tile_count_mismatches": tile_count_mismatches,
        },
        "current_map_dataset": ({
            "version": current.version,
            "status": current.status,
            "point_count": current.point_count,
            "tile_count": current.tile_count,
            "published_at": current.published_at,
        } if current else None),
        "map_delivery": map_delivery_reconciliation_report(
            session,
            verify_public_manifest=False,
            problem_limit=problem_limit,
        ),
        "last_sync": ({
            "id": last_run.id,
            "status": last_run.status,
            "started_at": last_run.started_at,
            "finished_at": last_run.finished_at,
        } if last_run else None),
        "problems": {"stale_active_lots": stale_lots},
    }


def map_delivery_reconciliation_report(
    session: Session,
    *,
    verify_public_manifest: bool = False,
    problem_limit: int = 100,
) -> dict[str, Any]:
    """Reconcile the public map across DB candidates, MapDataset and REG.RU S3.

    The DB eligibility calculation intentionally mirrors build_map_dataset.
    Because GEO and ingestion keep changing the live DB after a dataset starts
    building, rows changed after the dataset creation timestamp are reported as
    bounded post-dataset drift instead of false integrity failures. Older
    missing/extra rows still fail the reconciliation gate.
    """
    from bankrotai.core import get_settings
    from bankrotai.region_sanity import coordinate_region_sanity_rejection_reason
    from bankrotai.regions import normalize_region_code as normalize_canonical_region_code
    from bankrotai.services.map_builder import MAX_WEB_MERCATOR_LAT, POINT_ZOOM
    from bankrotai.services.map_bundle_store import normalize_map_region_code
    from bankrotai.services.map_dataset_version import MAP_DATASET_REVISION
    from bankrotai.services.map_object_store import _verify_public_manifest

    current_rows = session.scalars(
        select(MapDataset).where(MapDataset.is_current.is_(True))
    ).all()
    current = current_rows[0] if len(current_rows) == 1 else None
    if current is None:
        return {
            "ok": False,
            "current_dataset_count": len(current_rows),
            "error": "exactly one current map dataset is required",
            "db_candidate_count": 0,
            "db_eligible_count": 0,
            "spatially_rejected_count": 0,
            "spatial_rejection_reasons": {},
            "dataset_unique_lot_count": 0,
            "missing_from_dataset_count": 0,
            "extra_in_dataset_count": 0,
            "manifest": {"checked": False, "ok": None},
        }

    dataset_cutoff = current.created_at
    if dataset_cutoff.tzinfo is not None:
        dataset_cutoff = dataset_cutoff.astimezone(timezone.utc).replace(tzinfo=None)

    def changed_after_dataset(*values: datetime | None) -> bool:
        for value in values:
            if value is None:
                continue
            stamp = value
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            if stamp > dataset_cutoff:
                return True
        return False

    candidates = session.execute(
        select(
            ProcessedLot.id,
            ProcessedLot.region_code,
            ProcessedLot.cadastral_number,
            ProcessedLot.current_geo_lat,
            ProcessedLot.current_geo_lon,
            ProcessedLot.current_geo_observed_at,
            ProcessedLot.last_update,
        ).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
            ProcessedLot.current_geo_lon.between(-180.0, 180.0),
        )
    ).all()

    eligible_ids: set[int] = set()
    eligible_change_times: dict[int, tuple[datetime | None, datetime | None]] = {}
    spatial_rejections: dict[str, int] = {}
    spatial_rejected_ids: list[int] = []
    for row in candidates:
        lat = float(row.current_geo_lat)
        lon = float(row.current_geo_lon)
        raw_rejection = coordinate_region_sanity_rejection_reason(lat, lon, row.region_code)
        reason: str | None
        if raw_rejection == "unsupported_region_code":
            reason = raw_rejection
        else:
            bundle_region_code = normalize_map_region_code(row.region_code, row.cadastral_number)
            bundle_rejection = coordinate_region_sanity_rejection_reason(lat, lon, bundle_region_code)
            if bundle_rejection:
                reason = (
                    "unsupported_bundle_region_code"
                    if bundle_rejection == "unsupported_region_code"
                    else f"bundle_{bundle_rejection}"
                )
            else:
                region_code = (
                    normalize_canonical_region_code(bundle_region_code)
                    or normalize_canonical_region_code(row.region_code)
                    or row.region_code
                )
                reason = coordinate_region_sanity_rejection_reason(lat, lon, region_code)

        if reason:
            spatial_rejections[reason] = spatial_rejections.get(reason, 0) + 1
            if len(spatial_rejected_ids) < max(1, problem_limit):
                spatial_rejected_ids.append(int(row.id))
            continue
        lot_id = int(row.id)
        eligible_ids.add(lot_id)
        eligible_change_times[lot_id] = (row.current_geo_observed_at, row.last_update)

    dataset_ids: set[int] = set()
    point_features = 0
    for payload in session.scalars(
        select(MapTile.payload_json).where(
            MapTile.dataset_id == current.id,
            MapTile.z == POINT_ZOOM,
        )
    ):
        for feature in (payload or {}).get("features", []):
            if not isinstance(feature, dict) or feature.get("kind") != "lot":
                continue
            feature_lot_id = feature.get("id")
            if isinstance(feature_lot_id, int):
                point_features += 1
                dataset_ids.add(feature_lot_id)

    live_missing_ids = sorted(eligible_ids - dataset_ids)
    live_extra_ids = sorted(dataset_ids - eligible_ids)

    pending_missing_ids = [
        lot_id
        for lot_id in live_missing_ids
        if changed_after_dataset(*eligible_change_times.get(lot_id, (None, None)))
    ]
    pending_missing_set = set(pending_missing_ids)
    missing_ids = [lot_id for lot_id in live_missing_ids if lot_id not in pending_missing_set]

    extra_change_times: dict[int, tuple[datetime | None, datetime | None, datetime | None]] = {}
    if live_extra_ids:
        for row in session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.current_geo_observed_at,
                ProcessedLot.last_update,
                ProcessedLot.archived_at,
            ).where(ProcessedLot.id.in_(live_extra_ids))
        ).all():
            extra_change_times[int(row.id)] = (
                row.current_geo_observed_at,
                row.last_update,
                row.archived_at,
            )
    pending_extra_ids = [
        lot_id
        for lot_id in live_extra_ids
        if lot_id in extra_change_times and changed_after_dataset(*extra_change_times[lot_id])
    ]
    pending_extra_set = set(pending_extra_ids)
    extra_ids = [lot_id for lot_id in live_extra_ids if lot_id not in pending_extra_set]

    actual_tile_count = int(
        session.scalar(
            select(func.count()).select_from(MapTile).where(MapTile.dataset_id == current.id)
        )
        or 0
    )
    point_count_matches = int(current.point_count or 0) == len(dataset_ids) == point_features
    tile_count_matches = int(current.tile_count or 0) == actual_tile_count

    manifest: dict[str, Any] = {"checked": False, "ok": None}
    if verify_public_manifest:
        settings = get_settings()
        if settings.map_object_store_enabled and current.version.endswith("-s3"):
            try:
                payload = _verify_public_manifest(settings, current.version)
                manifest_ok = (
                    payload.get("version") == current.version
                    and payload.get("pipeline_revision") == MAP_DATASET_REVISION
                    and int(payload.get("point_count", -1)) == int(current.point_count or 0)
                    and int(payload.get("tile_count", -1)) == int(current.tile_count or 0)
                )
                manifest = {
                    "checked": True,
                    "ok": manifest_ok,
                    "version": payload.get("version"),
                    "pipeline_revision": payload.get("pipeline_revision"),
                    "point_count": payload.get("point_count"),
                    "tile_count": payload.get("tile_count"),
                }
            except Exception as exc:
                manifest = {
                    "checked": True,
                    "ok": False,
                    "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
                }
        else:
            manifest = {
                "checked": True,
                "ok": False,
                "error": "current dataset is not configured for public object-store verification",
            }

    manifest_failed = manifest.get("checked") is True and manifest.get("ok") is not True
    ok = (
        len(current_rows) == 1
        and current.status == "ready"
        and current.published_at is not None
        and point_count_matches
        and tile_count_matches
        and not missing_ids
        and not extra_ids
        and not manifest_failed
    )
    return {
        "ok": ok,
        "current_dataset_count": len(current_rows),
        "dataset": {
            "version": current.version,
            "status": current.status,
            "created_at": current.created_at,
            "published_at": current.published_at,
            "declared_point_count": int(current.point_count or 0),
            "declared_tile_count": int(current.tile_count or 0),
            "actual_tile_count": actual_tile_count,
        },
        "db_candidate_count": len(candidates),
        "db_eligible_count": len(eligible_ids),
        "spatially_rejected_count": sum(spatial_rejections.values()),
        "spatial_rejection_reasons": dict(sorted(spatial_rejections.items())),
        "spatial_rejected_sample_lot_ids": spatial_rejected_ids,
        "dataset_unique_lot_count": len(dataset_ids),
        "dataset_point_feature_count": point_features,
        "point_count_matches": point_count_matches,
        "tile_count_matches": tile_count_matches,
        "missing_from_dataset_count": len(missing_ids),
        "missing_from_dataset_sample_lot_ids": missing_ids[: max(1, problem_limit)],
        "extra_in_dataset_count": len(extra_ids),
        "extra_in_dataset_sample_lot_ids": extra_ids[: max(1, problem_limit)],
        "live_missing_from_dataset_count": len(live_missing_ids),
        "live_extra_in_dataset_count": len(live_extra_ids),
        "pending_post_dataset_missing_count": len(pending_missing_ids),
        "pending_post_dataset_missing_sample_lot_ids": pending_missing_ids[: max(1, problem_limit)],
        "pending_post_dataset_extra_count": len(pending_extra_ids),
        "pending_post_dataset_extra_sample_lot_ids": pending_extra_ids[: max(1, problem_limit)],
        "manifest": manifest,
    }


_SOURCE_FRESH_SECONDS = 2 * 3600
_SOURCE_DELAYED_SECONDS = 6 * 3600
_SOURCE_COMPLETE_FRESH_SECONDS = 36 * 3600


def _source_age_seconds(now: Any, value: Any) -> int | None:
    if value is None:
        return None

    def naive_utc(item: Any) -> Any:
        if getattr(item, "tzinfo", None) is not None and item.utcoffset() is not None:
            return item.astimezone(timezone.utc).replace(tzinfo=None)
        return item.replace(tzinfo=None)

    return max(0, int((naive_utc(now) - naive_utc(value)).total_seconds()))


def _source_error_category(error: str | None) -> str | None:
    if not error:
        return None
    message = error.casefold()
    if any(
        marker in message
        for marker in (
            "uniqueviolation",
            "integrityerror",
            "duplicate key value violates unique constraint",
            "psycopg.errors",
        )
    ):
        return "database_integrity"
    if any(
        marker in message
        for marker in (
            "traceback (most recent call last)",
            "attributeerror",
            "typeerror",
            "keyerror",
        )
    ):
        return "internal_error"
    if "coverage guard" in message:
        return "coverage_guard"
    if "access_limited" in message:
        return "access_limited"
    if "429" in message or "rate limit" in message or "too many requests" in message:
        return "rate_limit"
    if any(marker in message for marker in ("401", "403", "unauthorized", "forbidden", "authentication")):
        return "authentication"
    if any(marker in message for marker in ("timeout", "timed out")):
        return "timeout"
    if any(marker in message for marker in ("502", "503", "504", "connection", "dns", "ssl", "tls", "network")):
        return "upstream_network"
    if any(marker in message for marker in ("validation", "invalid")):
        return "validation"
    return "upstream_error"


def list_source_health(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[SourceHealthDTO]:
    states = {row.source_system: row for row in session.scalars(select(SourceHealthState)).all()}
    counts = dict(
        session.execute(select(ProcessedLot.source_system, func.count()).group_by(ProcessedLot.source_system)).all()
    )
    run_names = set(session.scalars(select(LotSyncSourceRun.source_system).distinct()).all())
    names = sorted(set(states) | set(counts) | run_names)
    now = now or utc_now()

    values: list[SourceHealthDTO] = []
    for name in names:
        latest = session.scalar(
            select(LotSyncSourceRun)
            .where(LotSyncSourceRun.source_system == name)
            .order_by(LotSyncSourceRun.started_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_success = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "success",
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_complete = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "success",
                LotSyncSourceRun.complete_source_run.is_(True),
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        latest_failure = session.scalar(
            select(LotSyncSourceRun)
            .where(
                LotSyncSourceRun.source_system == name,
                LotSyncSourceRun.status == "failed",
                LotSyncSourceRun.finished_at.isnot(None),
            )
            .order_by(LotSyncSourceRun.finished_at.desc(), LotSyncSourceRun.id.desc())
            .limit(1)
        )
        state = states.get(name)
        resilience_metadata = dict(state.metadata_json or {}) if state is not None else {}
        resilience = source_resilience_status(session, name, now=now)
        last_success_at = (
            latest_success.finished_at
            if latest_success is not None
            else state.last_success_at if state is not None else None
        )
        last_failure_at = (
            latest_failure.finished_at
            if latest_failure is not None
            else state.last_failure_at if state is not None else None
        )
        last_error = (
            latest_failure.error_message
            if latest_failure is not None
            else state.last_error if state is not None else None
        )
        freshness_age = _source_age_seconds(now, last_success_at)
        complete_age = _source_age_seconds(
            now,
            latest_complete.finished_at if latest_complete is not None else None,
        )

        if latest is not None and latest.status in {"running", "queued"}:
            freshness_status = "running"
        elif latest is not None and latest.status == "failed":
            freshness_status = "failed"
        elif freshness_age is None:
            freshness_status = "stale"
        elif freshness_age <= _SOURCE_FRESH_SECONDS:
            freshness_status = "fresh"
        elif freshness_age <= _SOURCE_DELAYED_SECONDS:
            freshness_status = "delayed"
        else:
            freshness_status = "stale"

        if complete_age is None:
            coverage_status = "missing"
        elif complete_age <= _SOURCE_COMPLETE_FRESH_SECONDS:
            coverage_status = "fresh"
        else:
            coverage_status = "stale"

        if latest is not None:
            status = (
                "healthy"
                if latest.status == "success" and latest.complete_source_run
                else "partial"
                if latest.status == "success"
                else latest.status
            )
        else:
            status = state.status if state is not None else "not_checked"

        values.append(
            SourceHealthDTO(
                source_system=name,
                status=status,
                items_seen=(
                    latest.items_seen
                    if latest is not None
                    else state.items_seen if state is not None else int(counts.get(name, 0))
                ),
                last_attempt_at=(
                    latest.started_at
                    if latest is not None
                    else state.last_started_at if state is not None else None
                ),
                last_success_at=last_success_at,
                last_complete_success_at=latest_complete.finished_at if latest_complete is not None else None,
                last_failure_at=last_failure_at,
                last_error=last_error,
                last_error_category=(
                    resilience.get("last_error_category")
                    if "last_error_category" in resilience_metadata
                    else _source_error_category(last_error)
                ),
                circuit_state=str(resilience.get("circuit_state") or "closed"),
                circuit_open_until=resilience.get("circuit_open_until"),
                next_retry_at=resilience.get("next_retry_at"),
                retryable=bool(resilience.get("retryable")),
                operational_failure=bool(resilience.get("operational_failure")),
                consecutive_operational_failures=int(
                    resilience.get("consecutive_operational_failures") or 0
                ),
                last_probe_at=resilience.get("last_probe_at"),
                last_probe_success_at=resilience.get("last_probe_success_at"),
                network_fingerprint=dict(resilience.get("network_fingerprint") or {}),
                freshness_status=freshness_status,
                coverage_status=coverage_status,
                freshness_age_seconds=freshness_age,
                complete_snapshot_age_seconds=complete_age,
                last_duration_ms=latest.duration_ms if latest is not None else None,
                last_complete_source_run=bool(latest.complete_source_run) if latest is not None else False,
                last_pages_scanned=int(latest.pages_scanned) if latest is not None else 0,
                last_items_inserted=int(latest.items_inserted) if latest is not None else 0,
                last_items_updated=int(latest.items_updated) if latest is not None else 0,
                last_items_unchanged=int(latest.items_unchanged) if latest is not None else 0,
                last_items_archived=int(latest.items_archived) if latest is not None else 0,
                last_items_failed=int(latest.items_failed) if latest is not None else 0,
            )
        )
    return values


def update_source_health(
    session: Session,
    source_system: str,
    *,
    status: str,
    items_seen: int | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceHealthState:
    state = session.scalar(select(SourceHealthState).where(SourceHealthState.source_system == source_system))
    if state is None:
        state = SourceHealthState(source_system=source_system)
        session.add(state)
    now = utc_now()
    state.status = status
    state.updated_at = now
    if metadata is not None:
        state.metadata_json = {**(state.metadata_json or {}), **metadata}
    if status in {"running", "queued"}:
        state.last_started_at = now
    elif status == "healthy":
        state.last_success_at = now
        state.last_error = None
        if items_seen is not None:
            state.items_seen = max(0, items_seen)
    elif status == "failed":
        state.last_failure_at = now
        state.last_error = (error or "Unknown source error")[:5000]
    session.flush()
    return state


def record_geo_failure(
    session: Session,
    lot_id: int,
    error: str,
    *,
    retry_after_seconds: int = 300,
) -> GeoFailure:
    failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
    now = utc_now()
    if failure is None:
        failure = GeoFailure(lot_id=lot_id, error_message=error)
        session.add(failure)
    else:
        failure.attempt_count += 1
        failure.error_message = error
    failure.status = "queued"
    failure.last_failed_at = now
    failure.next_retry_at = now + timedelta(seconds=max(0, retry_after_seconds))
    failure.resolved_at = None
    lot = session.get(ProcessedLot, lot_id)
    if lot is not None:
        lot.needs_geo_check = True
    session.flush()
    return failure


def resolve_geo_failure(session: Session, lot_id: int) -> bool:
    failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
    if failure is None:
        return False
    failure.status = "resolved"
    failure.resolved_at = utc_now()
    failure.next_retry_at = None
    lot = session.get(ProcessedLot, lot_id)
    if lot is not None:
        lot.needs_geo_check = False
    session.flush()
    return True


def geo_retry_lot_ids(session: Session, *, limit: int = 100) -> list[int]:
    now = utc_now()
    return list(
        session.scalars(
            select(GeoFailure.lot_id)
            .where(GeoFailure.status.not_in(("resolved", "terminal")))
            .where((GeoFailure.next_retry_at.is_(None)) | (GeoFailure.next_retry_at <= now))
            .order_by(GeoFailure.last_failed_at)
            .limit(max(1, min(limit, 1000)))
        ).all()
    )


def apply_raw_payload_retention(session: Session, *, retention_days: int = 30) -> dict[str, int]:
    cutoff = utc_now() - timedelta(days=max(1, retention_days))
    raw_deleted = session.execute(delete(RawLot).where(RawLot.created_at < cutoff)).rowcount or 0
    source_cleared = (
        session.execute(
            update(SourceLot).where(SourceLot.created_at < cutoff, SourceLot.raw_data.isnot(None)).values(raw_data=None)
        ).rowcount
        or 0
    )
    session.add(
        DiagnosticEvent(
            severity="info",
            component="retention",
            message="Raw payload retention completed",
            context_json={
                "retention_days": retention_days,
                "raw_deleted": raw_deleted,
                "source_cleared": source_cleared,
            },
        )
    )
    return {"raw_deleted": raw_deleted, "source_cleared": source_cleared}


def record_diagnostic(
    session: Session,
    *,
    severity: str,
    component: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> DiagnosticEvent:
    event = DiagnosticEvent(
        severity=severity,
        component=component,
        message=message,
        context_json=context,
    )
    session.add(event)
    session.flush()
    return event
