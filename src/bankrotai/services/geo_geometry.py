"""Pure GeoJSON geometry utilities for cadastral and geocoding providers.

Extracted from geo.py (BAT-308), preserving its public import surface and
coordinate-system heuristics. No network, database or provider-specific state.
"""

from __future__ import annotations

from typing import Any
import math


def centroid_from_geometry(geom: dict | None) -> tuple[float | None, float | None]:
    if not geom:
        return None, None

    coords = geom.get("coordinates")
    if not coords:
        return None, None

    points = []

    def collect(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and all(isinstance(x, (int, float)) for x in obj[:2]):
                points.append(obj)
            else:
                for item in obj:
                    collect(item)

    collect(coords)

    if not points:
        return None, None

    lon = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)

    return lat, lon


def to_geojson_geometry(geom: dict | None) -> dict | None:
    if not geom:
        return None

    if geom.get("type") and geom.get("coordinates"):
        return {
            "type": geom["type"],
            "coordinates": geom["coordinates"],
        }

    return None


def json_like_text(value: Any) -> str:
    return str(value)


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    radius = 6378137.0
    lon = (x / radius) * 180.0 / math.pi
    lat = math.degrees(math.atan(math.sinh(y / radius)))
    return lon, lat


def geometry_to_wgs84(geom: dict | None) -> dict | None:
    if not geom:
        return None

    coords = geom.get("coordinates")
    if not coords:
        return to_geojson_geometry(geom)

    def convert(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and all(isinstance(x, (int, float)) for x in obj[:2]):
                x, y = float(obj[0]), float(obj[1])
                if abs(x) > 180 or abs(y) > 90:
                    return list(web_mercator_to_wgs84(x, y))
                return [x, y]
            return [convert(item) for item in obj]
        return obj

    return {
        "type": geom.get("type"),
        "coordinates": convert(coords),
    }
