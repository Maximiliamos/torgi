from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.db import Base, LotGeoSnapshot, MapDataset, MapTile, ProcessedLot
from bankrotai.services.map_builder import build_map_dataset, tile_xy
from bankrotai.services.map_runtime import (
    build_filtered_tile,
    get_runtime_index,
    reset_runtime_index_for_tests,
)


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    rows = [
        ("a", "76", Decimal("1000000"), 57.6261, 39.8845),
        ("b", "76", Decimal("2000000"), 57.6262, 39.8846),
        ("c", "77", Decimal("3000000"), 55.7558, 37.6173),
    ]
    with factory() as session:
        for external_id, region_code, price, lat, lon in rows:
            lot = ProcessedLot(
                external_id=external_id,
                source="test",
                source_system="test",
                title=f"Lot {external_id}",
                description="",
                category="land",
                region_code=region_code,
                start_price=price,
                current_price=price,
                auction_status="active",
            )
            session.add(lot)
            session.flush()
            session.add(
                LotGeoSnapshot(
                    lot_id=lot.id,
                    geo_source="test",
                    geo_method="fixture",
                    geo_confidence="high",
                    centroid_lat=lat,
                    centroid_lon=lon,
                )
            )
        session.commit()
    return factory


def test_runtime_index_filters_region_and_price_without_source_join():
    reset_runtime_index_for_tests()
    factory = _factory()
    result = build_map_dataset(factory)
    index = get_runtime_index(factory, result["version"])

    x, y = tile_xy(57.6261, 39.8845, 12)
    region_tile = build_filtered_tile(
        index,
        z=12,
        x=x,
        y=y,
        region_code="76",
        min_start_price=1_500_000,
        max_start_price=None,
    )
    ids = {int(feature["id"]) for feature in region_tile["features"]}
    assert len(ids) == 1
    assert region_tile["type"] == "FeatureCollection"
    feature = region_tile["features"][0]
    assert feature["properties"]["region_code"] == "76"
    assert feature["properties"]["start_price"] == 2_000_000.0

    empty = build_filtered_tile(
        index,
        z=12,
        x=x,
        y=y,
        region_code="77",
        min_start_price=None,
        max_start_price=None,
    )
    assert empty == {"type": "FeatureCollection", "features": []}


def test_runtime_index_reclusters_filtered_points_at_low_zoom():
    reset_runtime_index_for_tests()
    factory = _factory()
    result = build_map_dataset(factory)
    index = get_runtime_index(factory, result["version"])

    x, y = tile_xy(57.6261, 39.8845, 7)
    payload = build_filtered_tile(
        index,
        z=7,
        x=x,
        y=y,
        region_code="76",
        min_start_price=None,
        max_start_price=2_000_000,
    )

    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 1
    cluster = payload["features"][0]
    assert cluster["properties"]["kind"] == "cluster"
    assert cluster["properties"]["count"] == 2
    assert cluster["properties"]["bounds"]
    assert str(cluster["id"]).startswith(f"fc:{result['version']}:7:")


def test_runtime_index_recovers_region_from_legacy_point_tiles():
    reset_runtime_index_for_tests()
    factory = _factory()
    result = build_map_dataset(factory)

    with factory() as session:
        dataset = session.scalar(
            select(MapDataset).where(MapDataset.version == result["version"])
        )
        assert dataset is not None
        tiles = session.scalars(
            select(MapTile).where(MapTile.dataset_id == dataset.id, MapTile.z == 12)
        ).all()
        for tile in tiles:
            payload = dict(tile.payload_json)
            payload.pop("yandex", None)
            tile.payload_json = payload
        session.commit()

    reset_runtime_index_for_tests()
    index = get_runtime_index(factory, result["version"])
    assert {point.region_code for bucket in index.by_zoom_tile[12].values() for point in bucket} >= {"76", "77"}


def test_runtime_index_switches_atomically_to_a_new_dataset_version():
    reset_runtime_index_for_tests()
    factory = _factory()
    first = build_map_dataset(factory)
    first_index = get_runtime_index(factory, first["version"])
    assert first_index.point_count == 3

    with factory() as session:
        lot = ProcessedLot(
            external_id="d",
            source="test",
            source_system="test",
            title="Lot d",
            description="",
            category="land",
            region_code="78",
            start_price=Decimal("4000000"),
            auction_status="active",
        )
        session.add(lot)
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="test",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=59.93,
                centroid_lon=30.33,
            )
        )
        session.commit()

    second = build_map_dataset(factory)
    second_index = get_runtime_index(factory, second["version"])
    assert second_index.version == second["version"]
    assert second_index.point_count == 4


def test_filtered_tile_api_uses_runtime_index_and_etag(monkeypatch):
    reset_runtime_index_for_tests()
    factory = _factory()
    result = build_map_dataset(factory)

    @contextmanager
    def scope():
        with factory() as session:
            yield session

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api.settings, "app_env", "test")

    x, y = tile_xy(57.6261, 39.8845, 12)
    client = TestClient(api.app)
    response = client.get(
        f"/api/map/filtered-tiles/{result['version']}/12/{x}/{y}",
        params={"region_code": "76", "min_start_price": 1_500_000},
    )
    assert response.status_code == 200
    assert response.headers["x-map-index"] == "runtime"
    assert response.headers["x-map-dataset"] == result["version"]
    assert response.headers["cache-control"] == "private, max-age=30, stale-while-revalidate=60"
    assert response.json()["type"] == "FeatureCollection"
    assert len(response.json()["features"]) == 1

    not_modified = client.get(
        f"/api/map/filtered-tiles/{result['version']}/12/{x}/{y}",
        params={"region_code": "76", "min_start_price": 1_500_000},
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert not_modified.status_code == 304

    invalid = client.get(
        f"/api/map/filtered-tiles/{result['version']}/12/{x}/{y}",
        params={"min_start_price": 10, "max_start_price": 1},
    )
    assert invalid.status_code == 422
