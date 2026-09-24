from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import logging
from threading import RLock, Thread
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import MapDataset, MapTile, ProcessedLot
from bankrotai.services.map_builder import MAX_DATASET_ZOOM, POINT_ZOOM, tile_bounds, tile_xy
from bankrotai.services.map_payload import (
    legacy_tile_to_yandex,
    yandex_cluster_feature,
    yandex_feature_collection,
    yandex_lot_feature,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimePoint:
    id: int
    lat: float
    lon: float
    title: str | None
    current_price: float | None
    start_price: float | None
    region_code: str | None
    status: str | None
    review_status: str | None

    def as_payload_point(self) -> dict[str, Any]:
        return {
            "kind": "lot",
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "title": self.title,
            "current_price": self.current_price,
            "start_price": self.start_price,
            "region_code": self.region_code,
            "status": self.status,
            "is_archived": False,
            "review_status": self.review_status,
        }


@dataclass(frozen=True, slots=True)
class RuntimeMapIndex:
    version: str
    point_count: int
    by_zoom_tile: dict[int, dict[tuple[int, int], tuple[RuntimePoint, ...]]]


_runtime_lock = RLock()
_runtime_index: RuntimeMapIndex | None = None
_warming_versions: set[str] = set()


def _finite_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _point_from_yandex_feature(feature: dict[str, Any]) -> RuntimePoint | None:
    if feature.get("type") != "Feature":
        return None
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or properties.get("kind") != "lot":
        return None
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        return None
    lat = _finite_number(coordinates[0])
    lon = _finite_number(coordinates[1])
    lot_id = feature.get("id")
    if lat is None or lon is None or not isinstance(lot_id, int):
        return None
    return RuntimePoint(
        id=lot_id,
        lat=lat,
        lon=lon,
        title=properties.get("title") if isinstance(properties.get("title"), str) else None,
        current_price=_finite_number(properties.get("current_price")),
        start_price=_finite_number(properties.get("start_price")),
        region_code=str(properties["region_code"]) if properties.get("region_code") not in {None, ""} else None,
        status=str(properties["status"]) if properties.get("status") not in {None, ""} else None,
        review_status=(
            str(properties["review_status"])
            if properties.get("review_status") not in {None, ""}
            else None
        ),
    )


def _point_from_legacy_feature(feature: dict[str, Any]) -> RuntimePoint | None:
    if feature.get("kind") != "lot" or not isinstance(feature.get("id"), int):
        return None
    lat = _finite_number(feature.get("lat"))
    lon = _finite_number(feature.get("lon"))
    if lat is None or lon is None:
        return None
    return RuntimePoint(
        id=int(feature["id"]),
        lat=lat,
        lon=lon,
        title=feature.get("title") if isinstance(feature.get("title"), str) else None,
        current_price=_finite_number(feature.get("current_price")),
        start_price=_finite_number(feature.get("start_price")),
        region_code=(
            str(feature["region_code"])
            if feature.get("region_code") not in {None, ""}
            else None
        ),
        status=str(feature["status"]) if feature.get("status") not in {None, ""} else None,
        review_status=(
            str(feature["review_status"])
            if feature.get("review_status") not in {None, ""}
            else None
        ),
    )


def _dataset_points(session: Session, dataset: MapDataset) -> list[RuntimePoint]:
    points_by_id: dict[int, RuntimePoint] = {}
    missing_region_ids: set[int] = set()

    payloads = session.scalars(
        select(MapTile.payload_json).where(
            MapTile.dataset_id == dataset.id,
            MapTile.z == POINT_ZOOM,
        )
    ).all()

    for payload in payloads:
        value = payload if isinstance(payload, dict) else {}
        ready = value.get("yandex")
        if isinstance(ready, dict) and isinstance(ready.get("features"), list):
            raw_features = ready["features"]
            parser = _point_from_yandex_feature
        else:
            raw_features = value.get("features") if isinstance(value.get("features"), list) else []
            parser = _point_from_legacy_feature

        for raw in raw_features:
            if not isinstance(raw, dict):
                continue
            point = parser(raw)
            if point is None:
                continue
            points_by_id[point.id] = point
            if point.region_code is None:
                missing_region_ids.add(point.id)

    if missing_region_ids:
        regions = dict(
            session.execute(
                select(ProcessedLot.id, ProcessedLot.region_code).where(
                    ProcessedLot.id.in_(missing_region_ids)
                )
            ).all()
        )
        for lot_id in missing_region_ids:
            point = points_by_id.get(lot_id)
            if point is None:
                continue
            region = regions.get(lot_id)
            if region in {None, ""}:
                continue
            points_by_id[lot_id] = RuntimePoint(
                id=point.id,
                lat=point.lat,
                lon=point.lon,
                title=point.title,
                current_price=point.current_price,
                start_price=point.start_price,
                region_code=str(region),
                status=point.status,
                review_status=point.review_status,
            )

    points = list(points_by_id.values())
    if len(points) != int(dataset.point_count):
        raise RuntimeError(
            f"Map runtime index point mismatch for {dataset.version}: "
            f"dataset={dataset.point_count} runtime={len(points)}"
        )
    return points


def build_runtime_index(session: Session, dataset: MapDataset) -> RuntimeMapIndex:
    points = _dataset_points(session, dataset)
    buckets: dict[int, dict[tuple[int, int], list[RuntimePoint]]] = {
        zoom: defaultdict(list)
        for zoom in range(MAX_DATASET_ZOOM + 1)
    }
    for point in points:
        for zoom in range(MAX_DATASET_ZOOM + 1):
            buckets[zoom][tile_xy(point.lat, point.lon, zoom)].append(point)

    return RuntimeMapIndex(
        version=dataset.version,
        point_count=len(points),
        by_zoom_tile={
            zoom: {
                coordinate: tuple(members)
                for coordinate, members in zoom_buckets.items()
            }
            for zoom, zoom_buckets in buckets.items()
        },
    )


def get_runtime_index(
    session_factory: Callable[[], Session],
    version: str,
) -> RuntimeMapIndex:
    global _runtime_index
    with _runtime_lock:
        if _runtime_index is not None and _runtime_index.version == version:
            return _runtime_index

        with session_factory() as session:
            dataset = session.scalar(
                select(MapDataset).where(
                    MapDataset.version == version,
                    MapDataset.status == "ready",
                    MapDataset.published_at.is_not(None),
                )
            )
            if dataset is None:
                raise LookupError("Map dataset not found")
            built = build_runtime_index(session, dataset)

        _runtime_index = built
        logger.info(
            "Map runtime index ready: version=%s points=%s",
            built.version,
            built.point_count,
        )
        return built


def schedule_runtime_index_warmup(
    session_factory: Callable[[], Session],
    version: str,
) -> bool:
    with _runtime_lock:
        if _runtime_index is not None and _runtime_index.version == version:
            return False
        if version in _warming_versions:
            return False
        _warming_versions.add(version)

    def warm() -> None:
        try:
            get_runtime_index(session_factory, version)
        except Exception:
            logger.exception("Map runtime index warmup failed for %s", version)
        finally:
            with _runtime_lock:
                _warming_versions.discard(version)

    Thread(
        target=warm,
        name=f"map-runtime-{version[:20]}",
        daemon=True,
    ).start()
    return True


def _filter_token(
    region_code: str | None,
    min_start_price: float | None,
    max_start_price: float | None,
) -> str:
    raw = f"{region_code or '*'}|{min_start_price if min_start_price is not None else '*'}|{max_start_price if max_start_price is not None else '*'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_filtered_tile(
    index: RuntimeMapIndex,
    *,
    z: int,
    x: int,
    y: int,
    region_code: str | None,
    min_start_price: float | None,
    max_start_price: float | None,
) -> dict[str, Any]:
    candidates = index.by_zoom_tile.get(z, {}).get((x, y), ())
    filtered: list[RuntimePoint] = []
    for point in candidates:
        if region_code and point.region_code != region_code:
            continue
        if min_start_price is not None and (
            point.start_price is None or point.start_price < min_start_price
        ):
            continue
        if max_start_price is not None and (
            point.start_price is None or point.start_price > max_start_price
        ):
            continue
        filtered.append(point)

    if not filtered:
        return yandex_feature_collection([])

    members = [point.as_payload_point() for point in filtered]
    if z < POINT_ZOOM:
        feature = yandex_cluster_feature(
            zoom=z,
            x=x,
            y=y,
            members=members,
            bounds=tile_bounds(z, x, y),
        )
        feature["id"] = (
            f"fc:{index.version}:{z}:{x}:{y}:"
            f"{_filter_token(region_code, min_start_price, max_start_price)}"
        )
        return yandex_feature_collection([feature])

    return yandex_feature_collection(
        [yandex_lot_feature(point) for point in members]
    )


def reset_runtime_index_for_tests() -> None:
    global _runtime_index
    with _runtime_lock:
        _runtime_index = None
        _warming_versions.clear()
