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

from bankrotai.db import LotGeoSnapshot, MapDataset, MapTile, ProcessedLot

MAX_DATASET_ZOOM = 14
POINT_ZOOM = 12
MAX_WEB_MERCATOR_LAT = 85.05112878
_PROMOTION_ADVISORY_LOCK_KEY = 4_367_936_669_506_901_092
logger = logging.getLogger(__name__)


def map_dataset_storage_statistics(session: Session) -> dict[str, int]:
    """Return retention diagnostics without deleting historical datasets."""
    datasets = session.execute(
        select(MapDataset.id, MapDataset.status, MapDataset.is_current)
    ).all()
    non_current_ids = [row.id for row in datasets if not row.is_current]
    failed_or_rejected_ids = [
        row.id for row in datasets if row.status in {"failed", "rejected"}
    ]

    def tile_count(dataset_ids: list[int] | None = None) -> int:
        statement = select(func.count(MapTile.id))
        if dataset_ids is not None:
            if not dataset_ids:
                return 0
            statement = statement.where(MapTile.dataset_id.in_(dataset_ids))
        return int(session.scalar(statement) or 0)

    return {
        "dataset_count": len(datasets),
        "current_dataset_count": sum(1 for row in datasets if row.is_current),
        "non_current_dataset_count": len(non_current_ids),
        "building_dataset_count": sum(1 for row in datasets if row.status == "building"),
        "ready_dataset_count": sum(1 for row in datasets if row.status == "ready"),
        "failed_dataset_count": sum(1 for row in datasets if row.status == "failed"),
        "rejected_dataset_count": sum(1 for row in datasets if row.status == "rejected"),
        "tile_count": tile_count(),
        "non_current_tile_count": tile_count(non_current_ids),
        "failed_or_rejected_tile_count": tile_count(failed_or_rejected_ids),
    }


def cleanup_map_datasets(
    session_factory: Callable[[], Session],
    *,
    retain_previous_ready: int = 1,
    min_age_hours: int = 168,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    """Find or remove old non-current map versions under the publisher lock."""
    if retain_previous_ready < 1:
        raise ValueError("retain_previous_ready must preserve at least one rollback dataset")
    if min_age_hours < 1:
        raise ValueError("min_age_hours must be positive")
    reference_time = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = reference_time.timestamp() - min_age_hours * 3600
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
        rollback_ids = {
            dataset.id
            for dataset in [
                item for item in datasets
                if item.status == "ready" and item.published_at is not None
            ][:retain_previous_ready]
        }
        candidates = [
            dataset for dataset in datasets
            if dataset.id not in rollback_ids
            and dataset.created_at.timestamp() <= cutoff
            and dataset.status in {"ready", "failed", "rejected", "building"}
        ]
        candidate_ids = [dataset.id for dataset in candidates]
        candidate_tiles = int(session.scalar(
            select(func.count(MapTile.id)).where(MapTile.dataset_id.in_(candidate_ids))
        ) or 0) if candidate_ids else 0
        result = {
            "dry_run": not apply,
            "retained_previous_ready": len(rollback_ids),
            "candidate_dataset_count": len(candidates),
            "candidate_tile_count": candidate_tiles,
            "candidate_versions": [dataset.version for dataset in candidates],
        }
        if apply and candidate_ids:
            session.execute(delete(MapTile).where(MapTile.dataset_id.in_(candidate_ids)))
            session.execute(delete(MapDataset).where(MapDataset.id.in_(candidate_ids)))
            session.commit()
        else:
            session.rollback()
        return result


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    scale = 1 << zoom
    x = int((lon + 180.0) / 360.0 * scale)
    bounded_lat = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(bounded_lat)
    y = int((1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def _tile_bounds(z: int, x: int, y: int) -> list[float]:
    scale = 1 << z
    west = x / scale * 360.0 - 180.0
    east = (x + 1) / scale * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / scale))))
    return [west, south, east, north]


def _encoded_tile(features: list[dict]) -> tuple[dict, str]:
    payload = {"features": features}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()


def _promote_map_dataset(
    session_factory: Callable[[], Session],
    *,
    dataset_id: int,
    expected_current_id: int | None,
) -> dict:
    """Serialize and atomically promote one completely built dataset."""
    with session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _PROMOTION_ADVISORY_LOCK_KEY},
            )
        current = session.scalar(
            select(MapDataset).where(MapDataset.is_current.is_(True)).with_for_update()
        )
        dataset = session.scalar(
            select(MapDataset).where(MapDataset.id == dataset_id).with_for_update()
        )
        if dataset is None:
            raise RuntimeError(f"Map dataset {dataset_id} disappeared before promotion")
        current_id = current.id if current is not None else None
        if current_id != expected_current_id:
            dataset.status = "rejected"
            session.commit()
            logger.warning(
                "Map dataset %s promotion rejected: current changed from %s to %s",
                dataset.version, expected_current_id, current_id,
            )
            return {
                "status": "rejected",
                "reason": "current_dataset_changed",
                "current_dataset_id": current_id,
            }

        actual_tile_count = session.scalar(
            select(func.count(MapTile.id)).where(MapTile.dataset_id == dataset.id)
        ) or 0
        if dataset.status != "building" or dataset.tile_count != actual_tile_count:
            raise RuntimeError(
                f"Map dataset {dataset.version} is incomplete: "
                f"status={dataset.status}, expected_tiles={dataset.tile_count}, actual_tiles={actual_tile_count}"
            )
        if current is not None and current.point_count > 0 and dataset.point_count == 0:
            dataset.status = "rejected"
            session.commit()
            logger.warning(
                "Map dataset %s promotion rejected by coverage guard: previous_points=%s, new_points=0",
                dataset.version, current.point_count,
            )
            return {
                "status": "rejected",
                "reason": "empty_dataset_would_replace_nonempty_current",
                "current_dataset_id": current.id,
            }

        session.execute(
            update(MapDataset).where(MapDataset.is_current.is_(True)).values(is_current=False)
        )
        session.flush()
        dataset.is_current = True
        dataset.status = "ready"
        dataset.published_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        logger.info(
            "Map dataset promotion succeeded: version=%s dataset_id=%s previous_dataset_id=%s",
            dataset.version, dataset.id, current_id,
        )
        return {"status": "published", "current_dataset_id": dataset.id}


def build_map_dataset(session_factory: Callable[[], Session]) -> dict:
    """Build a complete dataset, then atomically make it current."""
    started = time.monotonic()
    build_completed = False
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with session_factory() as session:
        expected_current_id = session.scalar(
            select(MapDataset.id).where(MapDataset.is_current.is_(True))
        )
        dataset = MapDataset(version=version, status="building", is_current=False)
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id
    logger.info(
        "Map dataset build started: version=%s dataset_id=%s expected_current_id=%s",
        version, dataset_id, expected_current_id,
    )

    try:
        with session_factory() as session:
            source_lot_count = int(session.scalar(select(func.count(ProcessedLot.id))) or 0)
            ranked_geo = (
                select(
                    LotGeoSnapshot.id.label("geo_id"),
                    LotGeoSnapshot.lot_id,
                    func.row_number().over(
                        partition_by=LotGeoSnapshot.lot_id,
                        order_by=(LotGeoSnapshot.observed_at.desc(), LotGeoSnapshot.id.desc()),
                    ).label("geo_rank"),
                )
                .subquery()
            )
            rows = session.execute(
                select(
                    ProcessedLot.id, ProcessedLot.title, ProcessedLot.current_price,
                    ProcessedLot.start_price, ProcessedLot.auction_status,
                    ProcessedLot.review_status, LotGeoSnapshot.centroid_lat, LotGeoSnapshot.centroid_lon,
                )
                .join(ranked_geo, ranked_geo.c.lot_id == ProcessedLot.id)
                .join(LotGeoSnapshot, LotGeoSnapshot.id == ranked_geo.c.geo_id)
                .where(
                    ranked_geo.c.geo_rank == 1,
                    ProcessedLot.duplicate_of_id.is_(None),
                    ProcessedLot.is_archived.is_(False),
                    LotGeoSnapshot.centroid_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
                    LotGeoSnapshot.centroid_lon.between(-180.0, 180.0),
                )
            ).all()
        points = [
            {
                "kind": "lot", "id": row.id, "lat": row.centroid_lat, "lon": row.centroid_lon,
                "title": row.title, "current_price": float(row.current_price) if row.current_price is not None else None,
                "start_price": float(row.start_price) if row.start_price is not None else None,
                "status": row.auction_status, "review_status": row.review_status,
            }
            for row in rows
        ]
        tile_count = 0
        with session_factory() as session:
            for zoom in range(MAX_DATASET_ZOOM + 1):
                buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
                for point in points:
                    buckets[_tile_xy(point["lat"], point["lon"], zoom)].append(point)
                for (x, y), members in buckets.items():
                    if zoom < POINT_ZOOM:
                        features = [{
                            "kind": "cluster", "id": f"c:{zoom}:{x}:{y}",
                            "lat": sum(item["lat"] for item in members) / len(members),
                            "lon": sum(item["lon"] for item in members) / len(members),
                            "count": len(members), "bounds": _tile_bounds(zoom, x, y),
                        }]
                    else:
                        features = members
                    payload, etag = _encoded_tile(features)
                    session.add(MapTile(
                        dataset_id=dataset_id, z=zoom, x=x, y=y,
                        feature_count=len(features), etag=etag, payload_json=payload,
                    ))
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
            version, source_lot_count, len(points), tile_count, build_duration_ms,
        )
        build_completed = True
        promotion = _promote_map_dataset(
            session_factory,
            dataset_id=dataset_id,
            expected_current_id=expected_current_id,
        )
        total_duration_ms = round((time.monotonic() - started) * 1000)
        promotion_status = str(promotion["status"])
        logger.info(
            "Map dataset promotion finished: version=%s build_status=success promotion_status=%s "
            "duration_ms=%s",
            version, promotion_status, total_duration_ms,
        )
        with session_factory() as session:
            storage = map_dataset_storage_statistics(session)
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
            **promotion,
        }
    except Exception:
        with session_factory() as session:
            dataset = session.get(MapDataset, dataset_id)
            if dataset is not None:
                dataset.status = "failed"
                session.commit()
        if build_completed:
            logger.exception(
                "Map dataset promotion failed after successful build: version=%s dataset_id=%s duration_ms=%s",
                version, dataset_id, round((time.monotonic() - started) * 1000),
            )
        else:
            logger.exception(
                "Map dataset build failed: version=%s dataset_id=%s duration_ms=%s",
                version, dataset_id, round((time.monotonic() - started) * 1000),
            )
        raise
