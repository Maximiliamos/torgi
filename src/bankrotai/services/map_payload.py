from __future__ import annotations

from typing import Any


ENDED_STATUSES = frozenset(
    {
        "closed",
        "completed",
        "cancelled",
        "canceled",
        "failed",
        "annulled",
        "archive",
        "archived",
    }
)

LOT_PRESETS = {
    "approved": "islands#greenDotIcon",
    "maybe": "islands#yellowDotIcon",
    "rejected": "islands#redDotIcon",
    "default": "islands#grayDotIcon",
    "ended": "islands#blackDotIcon",
}


def _is_ended(point: dict[str, Any]) -> bool:
    if bool(point.get("is_archived")):
        return True
    return str(point.get("status") or "").casefold() in ENDED_STATUSES


def lot_preset(point: dict[str, Any]) -> str:
    if _is_ended(point):
        return LOT_PRESETS["ended"]
    review = str(point.get("review_status") or "").casefold()
    return LOT_PRESETS.get(review, LOT_PRESETS["default"])


def yandex_lot_feature(point: dict[str, Any]) -> dict[str, Any]:
    lot_id = int(point["id"])
    return {
        "type": "Feature",
        "id": lot_id,
        "geometry": {
            "type": "Point",
            # Yandex Maps ObjectManager uses [latitude, longitude] here.
            "coordinates": [float(point["lat"]), float(point["lon"])],
        },
        "properties": {
            "kind": "lot",
            "lotId": lot_id,
            "title": point.get("title"),
            "hintContent": point.get("title") or "",
            "current_price": point.get("current_price"),
            "start_price": point.get("start_price"),
            "region_code": point.get("region_code"),
            "status": point.get("status"),
            "review_status": point.get("review_status"),
        },
        "options": {
            "preset": lot_preset(point),
        },
    }


def yandex_cluster_feature(
    *,
    zoom: int,
    x: int,
    y: int,
    members: list[dict[str, Any]],
    bounds: list[float],
) -> dict[str, Any]:
    if not members:
        raise ValueError("cluster members must not be empty")
    return {
        "type": "Feature",
        "id": f"c:{zoom}:{x}:{y}",
        "geometry": {
            "type": "Point",
            "coordinates": [
                sum(float(item["lat"]) for item in members) / len(members),
                sum(float(item["lon"]) for item in members) / len(members),
            ],
        },
        "properties": {
            "kind": "cluster",
            "count": len(members),
            "hintContent": f"{len(members)} лотов",
            "iconContent": str(len(members)),
            "bounds": bounds,
        },
        "options": {
            "preset": "islands#blueCircleIcon",
        },
    }


def yandex_feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def legacy_feature_to_yandex(feature: dict[str, Any]) -> dict[str, Any]:
    kind = feature.get("kind")
    if kind == "lot":
        return yandex_lot_feature(feature)
    if kind == "cluster":
        return {
            "type": "Feature",
            "id": str(feature["id"]),
            "geometry": {
                "type": "Point",
                "coordinates": [float(feature["lat"]), float(feature["lon"])],
            },
            "properties": {
                "kind": "cluster",
                "count": int(feature["count"]),
                "hintContent": f"{int(feature['count'])} лотов",
                "iconContent": str(int(feature["count"])),
                "bounds": feature["bounds"],
            },
            "options": {
                "preset": "islands#blueCircleIcon",
            },
        }
    raise ValueError(f"unsupported legacy map feature kind: {kind!r}")


def legacy_tile_to_yandex(payload: dict[str, Any] | None) -> dict[str, Any]:
    value = payload or {}
    ready = value.get("yandex")
    if (
        isinstance(ready, dict)
        and ready.get("type") == "FeatureCollection"
        and isinstance(ready.get("features"), list)
    ):
        return ready
    raw_features = value.get("features")
    if not isinstance(raw_features, list):
        raw_features = []
    return yandex_feature_collection(
        [
            legacy_feature_to_yandex(feature)
            for feature in raw_features
            if isinstance(feature, dict)
        ]
    )


def public_yandex_tile_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a browser-public tile without internal review metadata."""
    source = legacy_tile_to_yandex(payload)
    features: list[dict[str, Any]] = []
    for raw in source.get("features", []):
        if not isinstance(raw, dict):
            continue
        feature = {
            "type": raw.get("type"),
            "id": raw.get("id"),
            "geometry": raw.get("geometry"),
            "properties": dict(raw.get("properties") or {}),
            "options": dict(raw.get("options") or {}),
        }
        properties = feature["properties"]
        if properties.get("kind") == "lot":
            properties.pop("review_status", None)
            status = str(properties.get("status") or "").casefold()
            feature["options"] = {
                "preset": LOT_PRESETS["ended"] if status in ENDED_STATUSES else LOT_PRESETS["default"],
            }
        features.append(feature)
    return yandex_feature_collection(features)
