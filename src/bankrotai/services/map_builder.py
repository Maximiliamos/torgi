from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from bankrotai.core import get_settings
from bankrotai.region_sanity import coordinate_region_sanity_rejection_reason
from bankrotai.regions import normalize_region_code as normalize_canonical_region_code
from bankrotai.db import MapDataset, MapTile, ProcessedLot
from bankrotai.services.map_payload import (
    yandex_cluster_feature,
    yandex_feature_collection,
    yandex_lot_feature,
)
from bankrotai.services.map_object_store import publish_dataset_to_object_store
from bankrotai.services.map_bundle_store import (
    REGIONAL_BUNDLE_LAYOUT,
    normalize_map_region_code,
    publish_dataset_to_regional_bundles,
)
from bankrotai.services.map_dataset_version import MAP_DATASET_REVISION, build_map_dataset_version

MAX_DATASET_ZOOM = 14
POINT_ZOOM = 12
MAX_WEB_MERCATOR_LAT = 85.05112878
_PROMOTION_ADVISORY_LOCK_KEY = 4_367_936_669_506_901_092
logger = logging.getLogger(__name__)
MIN_DIMENSION_COVERAGE_BASELINE = 20


def map_dataset_storage_statistics(session: Session) -> dict[str, int]:
    """Return retention diagnostics without scanning the large map_tiles table.

    MapDataset.tile_count is finalized from the actual inserted row count before
    publication/promotion. Summing that metadata keeps this post-promotion
    diagnostic bounded even when hundreds of historical datasets exist.
    """
    datasets = session.execute(
        select(MapDataset.id, MapDataset.status, MapDataset.is_current, MapDataset.tile_count)
    ).all()
    non_current = [row for row in datasets if not row.is_current]
    failed_or_rejected = [row for row in datasets if row.status in {"failed", "rejected"}]

    return {
        "dataset_count": len(datasets),
        "current_dataset_count": sum(1 for row in datasets if row.is_current),
        "non_current_dataset_count": len(non_current),
        "building_dataset_count": sum(1 for row in datasets if row.status == "building"),
        "ready_dataset_count": sum(1 for row in datasets if row.status == "ready"),
        "failed_dataset_count": sum(1 for row in datasets if row.status == "failed"),
        "rejected_dataset_count": sum(1 for row in datasets if row.status == "rejected"),
        "tile_count": sum(int(row.tile_count or 0) for row in datasets),
        "non_current_tile_count": sum(int(row.tile_count or 0) for row in non_current),
        "failed_or_rejected_tile_count": sum(int(row.tile_count or 0) for row in failed_or_rejected),
    }



def map_source_fingerprint(session: Session) -> str:
    """Return a cheap durable signature for map-affecting source state.

    The signature intentionally avoids scanning map_tiles. ProcessedLot.last_update
    changes on ordinary ORM updates, while row count/id sum cover membership
    changes. Geo observation time gives an additional guard for coordinate writes.
    """
    row = session.execute(
        select(
            func.count(ProcessedLot.id),
            func.coalesce(func.sum(ProcessedLot.id), 0),
            func.max(ProcessedLot.last_update),
            func.max(ProcessedLot.current_geo_observed_at),
        ).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
            ProcessedLot.current_geo_lon.between(-180.0, 180.0),
        )
    ).one()
    count, id_sum, last_update, last_geo = row
    settings = get_settings()
    payload = "|".join(
        (
            MAP_DATASET_REVISION,
            "1" if settings.map_object_store_enabled else "0",
            settings.map_object_store_layout,
            str(int(count or 0)),
            str(int(id_sum or 0)),
            last_update.isoformat() if last_update is not None else "",
            last_geo.isoformat() if last_geo is not None else "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def cleanup_map_datasets(
    session_factory: Callable[[], Session],
    *,
    retain_previous_ready: int = 2,
    min_age_hours: int = 1,
    building_min_age_hours: int = 6,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    """Find or remove obsolete non-current map versions under the publisher lock.

    Ready versions are bounded by count (current + rollback copies), rather than
    by age, so frequent publications cannot grow storage without bound. Failed
    and rejected builds retain a short diagnostic grace period, while abandoned
    building rows get a longer grace period to avoid racing a live build.
    """
    if retain_previous_ready < 1:
        raise ValueError("retain_previous_ready must preserve at least one rollback dataset")
    if min_age_hours < 1:
        raise ValueError("min_age_hours must be positive")
    if building_min_age_hours < min_age_hours:
        raise ValueError("building_min_age_hours must be >= min_age_hours")
    reference_time = now or datetime.now(timezone.utc).replace(tzinfo=None)
    stale_cutoff = reference_time.timestamp() - min_age_hours * 3600
    building_cutoff = reference_time.timestamp() - building_min_age_hours * 3600
    with session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _PROMOTION_ADVISORY_LOCK_KEY},
            )
        datasets = session.scalars(
            select(MapDataset)
            .where(MapDataset.is_current.is_(False))
            .order_by(MapDataset.published_at.desc(), MapDataset.created_at.desc(), MapDataset.id.desc())
        ).all()
        rollback_ready = [
            item for item in datasets
            if item.status == "ready" and item.published_at is not None
        ][:retain_previous_ready]
        rollback_ids = {dataset.id for dataset in rollback_ready}

        def is_candidate(dataset: MapDataset) -> bool:
            if dataset.id in rollback_ids:
                return False
            if dataset.status == "ready":
                # Count-based retention is the hard storage bound. Once a newer
                # rollback set exists, older ready datasets are redundant.
                return True
            created = dataset.created_at.timestamp()
            if dataset.status in {"failed", "rejected"}:
                return created <= stale_cutoff
            if dataset.status == "building":
                return created <= building_cutoff
            return False

        candidates = [dataset for dataset in datasets if is_candidate(dataset)]
        candidate_ids = [dataset.id for dataset in candidates]
        candidate_tiles = sum(int(dataset.tile_count or 0) for dataset in candidates)
        result = {
            "dry_run": not apply,
            "retained_previous_ready": len(rollback_ids),
            "retained_previous_ready_versions": [dataset.version for dataset in rollback_ready],
            "candidate_dataset_count": len(candidates),
            "candidate_tile_count": candidate_tiles,
            "candidate_versions": [dataset.version for dataset in candidates],
            "candidate_status_counts": {
                status: sum(1 for dataset in candidates if dataset.status == status)
                for status in ("ready", "failed", "rejected", "building")
            },
        }
        if apply and candidate_ids:
            # A single DELETE for millions of tile rows exceeds the production
            # statement timeout. Small transactions keep the API responsive and
            # remain restartable: already removed candidates simply disappear
            # from the next dry-run.
            deleted_dataset_count = 0
            deleted_tile_count = 0
            for offset in range(0, len(candidate_ids), 5):
                batch_ids = candidate_ids[offset : offset + 5]
                if session.get_bind().dialect.name == "postgresql":
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": _PROMOTION_ADVISORY_LOCK_KEY},
                    )
                deleted_tiles = session.execute(delete(MapTile).where(MapTile.dataset_id.in_(batch_ids))).rowcount or 0
                deleted_datasets = session.execute(delete(MapDataset).where(MapDataset.id.in_(batch_ids))).rowcount or 0
                session.commit()
                deleted_tile_count += int(deleted_tiles)
                deleted_dataset_count += int(deleted_datasets)
            result["deleted_tile_count"] = deleted_tile_count
            result["deleted_dataset_count"] = deleted_dataset_count
        else:
            session.rollback()
        return result

def tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    scale = 1 << zoom
    x = int((lon + 180.0) / 360.0 * scale)
    bounded_lat = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(bounded_lat)
    y = int((1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def tile_bounds(z: int, x: int, y: int) -> list[float]:
    scale = 1 << z
    west = x / scale * 360.0 - 180.0
    east = (x + 1) / scale * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / scale))))
    return [west, south, east, north]


# Keep private aliases while other code/tests transition to the public helpers.
_tile_xy = tile_xy
_tile_bounds = tile_bounds


def _encoded_tile(
    features: list[dict],
    yandex_features: list[dict],
) -> tuple[dict, str]:
    payload = {
        # Legacy payload remains intact until the direct-tile frontend rollout.
        "features": features,
        # New path is already in the exact ObjectManager FeatureCollection shape.
        "yandex": yandex_feature_collection(yandex_features),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()


def _dataset_lot_ids(session: Session, dataset_id: int) -> set[int]:
    lot_ids: set[int] = set()
    for payload in session.scalars(
        select(MapTile.payload_json).where(
            MapTile.dataset_id == dataset_id,
            MapTile.z == POINT_ZOOM,
        )
    ):
        for feature in (payload or {}).get("features", []):
            if feature.get("kind") == "lot" and isinstance(feature.get("id"), int):
                lot_ids.add(feature["id"])
    return lot_ids


def _dimension_coverage_failures(
    session: Session,
    *,
    current_dataset_id: int,
    new_dataset_id: int,
    minimum_ratio: float,
) -> list[dict[str, object]]:
    old_ids = _dataset_lot_ids(session, current_dataset_id)
    new_ids = _dataset_lot_ids(session, new_dataset_id)
    if not old_ids:
        return []
    old_counts: dict[tuple[str, str], int] = defaultdict(int)
    new_counts: dict[tuple[str, str], int] = defaultdict(int)
    for lot_id, source_system, region_code in session.execute(
        select(ProcessedLot.id, ProcessedLot.source_system, ProcessedLot.region_code)
    ):
        dimensions = (("source", source_system or "unknown"), ("region", region_code or "unknown"))
        if lot_id in old_ids:
            for dimension in dimensions:
                old_counts[dimension] += 1
        if lot_id in new_ids:
            for dimension in dimensions:
                new_counts[dimension] += 1
    failures = []
    for (kind, value), previous in sorted(old_counts.items()):
        if previous < MIN_DIMENSION_COVERAGE_BASELINE:
            continue
        current_count = new_counts.get((kind, value), 0)
        required = math.ceil(previous * minimum_ratio)
        if current_count < required:
            failures.append(
                {
                    "dimension": kind,
                    "value": value,
                    "previous": previous,
                    "current": current_count,
                    "required": required,
                }
            )
    return failures


def _promote_map_dataset(
    session_factory: Callable[[], Session],
    *,
    dataset_id: int,
    expected_current_id: int | None,
    dimension_failures: list[dict[str, object]] | None = None,
) -> dict:
    """Serialize and atomically promote one completely built dataset."""
    with session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _PROMOTION_ADVISORY_LOCK_KEY},
            )
            # The partial unique index is the final invariant, but concurrent
            # publishers must not race between clearing the old row and marking
            # the new row current. This transaction-level table lock conflicts
            # with another publisher while continuing to allow ordinary SELECTs.
            session.execute(text("LOCK TABLE map_datasets IN SHARE ROW EXCLUSIVE MODE"))
        current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)).with_for_update())
        dataset = session.scalar(select(MapDataset).where(MapDataset.id == dataset_id).with_for_update())
        if dataset is None:
            raise RuntimeError(f"Map dataset {dataset_id} disappeared before promotion")
        current_id = current.id if current is not None else None
        if current_id != expected_current_id:
            dataset.status = "rejected"
            session.commit()
            logger.warning(
                "Map dataset %s promotion rejected: current changed from %s to %s",
                dataset.version,
                expected_current_id,
                current_id,
            )
            return {
                "status": "rejected",
                "reason": "current_dataset_changed",
                "current_dataset_id": current_id,
            }

        actual_tile_count = session.scalar(select(func.count(MapTile.id)).where(MapTile.dataset_id == dataset.id)) or 0
        if dataset.status != "building" or dataset.tile_count != actual_tile_count:
            raise RuntimeError(
                f"Map dataset {dataset.version} is incomplete: "
                f"status={dataset.status}, expected_tiles={dataset.tile_count}, actual_tiles={actual_tile_count}"
            )
        settings = get_settings()
        minimum_points = 0
        if current is not None and current.point_count > 0:
            minimum_points = max(
                settings.min_map_points,
                math.ceil(current.point_count * settings.min_map_coverage_ratio),
            )
        if current is not None and current.point_count > 0 and dataset.point_count < minimum_points:
            dataset.status = "rejected"
            session.commit()
            logger.warning(
                "Map dataset %s promotion rejected by coverage guard: "
                "previous_points=%s new_points=%s required_points=%s ratio=%s",
                dataset.version,
                current.point_count,
                dataset.point_count,
                minimum_points,
                settings.min_map_coverage_ratio,
            )
            return {
                "status": "rejected",
                "reason": (
                    "empty_dataset_would_replace_nonempty_current"
                    if dataset.point_count == 0
                    else "dataset_coverage_below_threshold"
                ),
                "current_dataset_id": current.id,
                "previous_point_count": current.point_count,
                "new_point_count": dataset.point_count,
                "minimum_point_count": minimum_points,
            }

        if dimension_failures:
            dataset.status = "rejected"
            session.commit()
            logger.warning(
                "Map dataset %s promotion rejected by dimensional coverage guard: %s",
                dataset.version,
                dimension_failures,
            )
            return {
                "status": "rejected",
                "reason": "dataset_dimension_coverage_below_threshold",
                "current_dataset_id": current.id if current else None,
                "coverage_failures": dimension_failures,
            }

        # Keep both sides of the current-dataset switch as explicit SQL in the
        # advisory-locked transaction. Mixing a bulk UPDATE with later ORM
        # attribute flushing can let a stale loaded ``current`` instance write
        # ``is_current=True`` back during a concurrent promotion.
        published_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.execute(text("UPDATE map_datasets SET is_current = false WHERE is_current"))
        session.execute(
            text(
                "UPDATE map_datasets "
                "SET is_current = true, status = 'ready', published_at = :published_at "
                "WHERE id = :dataset_id"
            ),
            {"dataset_id": dataset.id, "published_at": published_at},
        )
        session.commit()
        logger.info(
            "Map dataset promotion succeeded: version=%s dataset_id=%s previous_dataset_id=%s",
            dataset.version,
            dataset.id,
            current_id,
        )
        return {"status": "published", "current_dataset_id": dataset.id}


def build_map_dataset(session_factory: Callable[[], Session]) -> dict:
    """Build a complete dataset, then atomically make it current."""
    started = time.monotonic()
    build_completed = False
    promotion_completed = False
    tile_count = 0
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    settings = get_settings()
    version = build_map_dataset_version(
        timestamp,
        object_store_enabled=settings.map_object_store_enabled,
        object_store_layout=settings.map_object_store_layout,
    )
    with session_factory() as session:
        expected_current_id = session.scalar(select(MapDataset.id).where(MapDataset.is_current.is_(True)))
        dataset = MapDataset(version=version, status="building", is_current=False)
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id
    logger.info(
        "Map dataset build started: version=%s dataset_id=%s expected_current_id=%s",
        version,
        dataset_id,
        expected_current_id,
    )

    try:
        with session_factory() as session:
            source_lot_count = int(session.scalar(select(func.count(ProcessedLot.id))) or 0)
            rows = session.execute(
                select(
                    ProcessedLot.id,
                    ProcessedLot.title,
                    ProcessedLot.current_price,
                    ProcessedLot.start_price,
                    ProcessedLot.region_code,
                    ProcessedLot.cadastral_number,
                    ProcessedLot.auction_status,
                    ProcessedLot.is_archived,
                    ProcessedLot.review_status,
                    ProcessedLot.current_geo_lat.label("centroid_lat"),
                    ProcessedLot.current_geo_lon.label("centroid_lon"),
                )
                .where(
                    ProcessedLot.duplicate_of_id.is_(None),
                    ProcessedLot.is_archived.is_(False),
                    ProcessedLot.current_geo_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
                    ProcessedLot.current_geo_lon.between(-180.0, 180.0),
                )
            ).all()
        points: list[dict] = []
        spatial_rejection_counts: dict[str, int] = {}
        for row in rows:
            raw_region_rejection = coordinate_region_sanity_rejection_reason(
                float(row.centroid_lat),
                float(row.centroid_lon),
                row.region_code,
            )
            if raw_region_rejection == "unsupported_region_code":
                spatial_rejection_counts[raw_region_rejection] = (
                    spatial_rejection_counts.get(raw_region_rejection, 0) + 1
                )
                continue
            bundle_region_code = normalize_map_region_code(
                row.region_code,
                row.cadastral_number,
            )
            bundle_region_rejection = coordinate_region_sanity_rejection_reason(
                float(row.centroid_lat),
                float(row.centroid_lon),
                bundle_region_code,
            )
            if bundle_region_rejection:
                diagnostic_reason = (
                    "unsupported_bundle_region_code"
                    if bundle_region_rejection == "unsupported_region_code"
                    else f"bundle_{bundle_region_rejection}"
                )
                spatial_rejection_counts[diagnostic_reason] = (
                    spatial_rejection_counts.get(diagnostic_reason, 0) + 1
                )
                continue

            region_code = (
                normalize_canonical_region_code(bundle_region_code)
                or normalize_canonical_region_code(row.region_code)
                or row.region_code
            )
            spatial_rejection = coordinate_region_sanity_rejection_reason(
                float(row.centroid_lat),
                float(row.centroid_lon),
                region_code,
            )
            if spatial_rejection:
                spatial_rejection_counts[spatial_rejection] = (
                    spatial_rejection_counts.get(spatial_rejection, 0) + 1
                )
                continue
            points.append(
                {
                    "kind": "lot",
                    "id": row.id,
                    "lat": row.centroid_lat,
                    "lon": row.centroid_lon,
                    "title": row.title,
                    "current_price": float(row.current_price) if row.current_price is not None else None,
                    "start_price": float(row.start_price) if row.start_price is not None else None,
                    "region_code": region_code,
                    "bundle_region_code": bundle_region_code,
                    "status": row.auction_status,
                    "is_archived": row.is_archived,
                    "review_status": row.review_status,
                }
            )
        if spatial_rejection_counts:
            logger.warning(
                "Map dataset excluded spatially invalid coordinates: counts=%s",
                spatial_rejection_counts,
            )
        with session_factory() as session:
            for zoom in range(MAX_DATASET_ZOOM + 1):
                buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
                for point in points:
                    buckets[tile_xy(point["lat"], point["lon"], zoom)].append(point)
                for (x, y), members in buckets.items():
                    if zoom < POINT_ZOOM:
                        bounds = tile_bounds(zoom, x, y)
                        features = [
                            {
                                "kind": "cluster",
                                "id": f"c:{zoom}:{x}:{y}",
                                "lat": sum(item["lat"] for item in members) / len(members),
                                "lon": sum(item["lon"] for item in members) / len(members),
                                "count": len(members),
                                "bounds": bounds,
                            }
                        ]
                        yandex_features = [
                            yandex_cluster_feature(
                                zoom=zoom,
                                x=x,
                                y=y,
                                members=members,
                                bounds=bounds,
                            )
                        ]
                    else:
                        # Preserve the exact legacy response shape for the currently
                        # deployed frontend while precomputing the next direct path.
                        features = [
                            {
                                "kind": "lot",
                                "id": item["id"],
                                "lat": item["lat"],
                                "lon": item["lon"],
                                "title": item["title"],
                                "current_price": item["current_price"],
                                "start_price": item["start_price"],
                                "status": item["status"],
                                "review_status": item["review_status"],
                            }
                            for item in members
                        ]
                        yandex_features = [yandex_lot_feature(item) for item in members]
                    payload, etag = _encoded_tile(features, yandex_features)
                    session.add(
                        MapTile(
                            dataset_id=dataset_id,
                            z=zoom,
                            x=x,
                            y=y,
                            feature_count=len(features),
                            etag=etag,
                            payload_json=payload,
                        )
                    )
                    tile_count += 1
                    if tile_count % 500 == 0:
                        session.commit()
                session.commit()

            dataset = session.get(MapDataset, dataset_id)
            assert dataset is not None
            dataset.point_count = len(points)
            dataset.tile_count = tile_count
            session.commit()
        build_duration_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "Map dataset build succeeded: version=%s source_lot_count=%s included_point_count=%s "
            "tile_count=%s duration_ms=%s",
            version,
            source_lot_count,
            len(points),
            tile_count,
            build_duration_ms,
        )
        build_completed = True
        dimension_failures: list[dict[str, object]] = []
        if expected_current_id is not None:
            with session_factory() as session:
                dimension_failures = _dimension_coverage_failures(
                    session,
                    current_dataset_id=expected_current_id,
                    new_dataset_id=dataset_id,
                    minimum_ratio=get_settings().min_map_coverage_ratio,
                )
        if settings.map_object_store_layout == REGIONAL_BUNDLE_LAYOUT:
            object_store = publish_dataset_to_regional_bundles(
                session_factory,
                dataset_id=dataset_id,
                version=version,
            )
        else:
            object_store = publish_dataset_to_object_store(
                session_factory,
                dataset_id=dataset_id,
                version=version,
            )
        promotion = _promote_map_dataset(
            session_factory,
            dataset_id=dataset_id,
            expected_current_id=expected_current_id,
            dimension_failures=dimension_failures,
        )
        promotion_completed = True
        total_duration_ms = round((time.monotonic() - started) * 1000)
        promotion_status = str(promotion["status"])
        logger.info(
            "Map dataset promotion finished: version=%s build_status=success promotion_status=%s duration_ms=%s",
            version,
            promotion_status,
            total_duration_ms,
        )
        try:
            with session_factory() as session:
                storage = map_dataset_storage_statistics(session)
        except Exception:
            # Publication/promotion is authoritative. Retention telemetry must
            # never retroactively invalidate an already promoted dataset.
            logger.exception(
                "Map dataset storage diagnostics failed after promotion: version=%s dataset_id=%s",
                version,
                dataset_id,
            )
            storage = {"diagnostics_unavailable": 1}
        return {
            "version": version,
            "build_status": "success",
            "promotion_status": promotion_status,
            "source_lot_count": source_lot_count,
            "point_count": len(points),
            "tile_count": tile_count,
            "build_duration_ms": build_duration_ms,
            "duration_ms": total_duration_ms,
            "storage": storage,
            "object_store": object_store,
            **promotion,
        }
    except Exception:
        if not promotion_completed:
            with session_factory() as session:
                dataset = session.get(MapDataset, dataset_id)
                if dataset is not None:
                    dataset.status = "failed"
                    try:
                        dataset.tile_count = int(
                            session.scalar(
                                select(func.count(MapTile.id)).where(MapTile.dataset_id == dataset_id)
                            )
                            or 0
                        )
                    except Exception:
                        # Preserve failure reporting even if the database itself is degraded.
                        dataset.tile_count = max(int(dataset.tile_count or 0), tile_count)
                    session.commit()
        if promotion_completed:
            logger.exception(
                "Map dataset post-promotion step failed: version=%s dataset_id=%s duration_ms=%s",
                version,
                dataset_id,
                round((time.monotonic() - started) * 1000),
            )
        elif build_completed:
            logger.exception(
                "Map dataset promotion failed after successful build: version=%s dataset_id=%s duration_ms=%s",
                version,
                dataset_id,
                round((time.monotonic() - started) * 1000),
            )
        else:
            logger.exception(
                "Map dataset build failed: version=%s dataset_id=%s duration_ms=%s",
                version,
                dataset_id,
                round((time.monotonic() - started) * 1000),
            )
        raise
