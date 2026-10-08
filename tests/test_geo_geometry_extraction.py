"""Regression coverage for pure GEO geometry extraction (BAT-308)."""

import math

import pytest

from bankrotai import geo
from bankrotai.services import geo_geometry


def test_legacy_geo_imports_preserve_function_identity() -> None:
    for symbol in (
        "centroid_from_geometry",
        "to_geojson_geometry",
        "json_like_text",
        "web_mercator_to_wgs84",
        "geometry_to_wgs84",
    ):
        assert getattr(geo, symbol) is getattr(geo_geometry, symbol)


def test_polygon_centroid_keeps_lat_lon_order_and_nested_inputs() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [[[38.0, 56.0], [40.0, 56.0], [40.0, 58.0], [38.0, 58.0]]],
        "provider_field": "excluded",
    }
    assert geo_geometry.centroid_from_geometry(polygon) == (57.0, 39.0)
    assert geo_geometry.to_geojson_geometry(polygon) == {
        "type": "Polygon",
        "coordinates": polygon["coordinates"],
    }
    assert geo_geometry.centroid_from_geometry(None) == (None, None)
    assert geo_geometry.to_geojson_geometry({}) is None


def test_mercator_origin_and_nested_geojson_conversion() -> None:
    assert geo_geometry.web_mercator_to_wgs84(0.0, 0.0) == (0.0, 0.0)
    radius = 6_378_137.0
    x = radius * math.pi / 2.0
    actual = geo_geometry.geometry_to_wgs84({
        "type": "MultiPoint",
        "coordinates": [[x, 0.0], [0.0, x]],
    })
    assert actual is not None
    assert actual["type"] == "MultiPoint"
    assert actual["coordinates"][0] == pytest.approx([90.0, 0.0])
    assert actual["coordinates"][1][0] == pytest.approx(0.0)
    assert actual["coordinates"][1][1] == pytest.approx(66.51326044, abs=0.00001)


def test_wgs84_coordinates_remain_unchanged_and_input_not_mutated() -> None:
    original = {"type": "LineString", "coordinates": [[37, 55], [38, 56]]}
    normalized = geo_geometry.geometry_to_wgs84(original)
    assert normalized == {"type": "LineString", "coordinates": [[37.0, 55.0], [38.0, 56.0]]}
    assert original["coordinates"] == [[37, 55], [38, 56]]
    assert geo_geometry.json_like_text({"x": 1}) == "{'x': 1}"
