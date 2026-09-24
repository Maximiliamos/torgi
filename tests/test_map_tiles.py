from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import upsert_user
from bankrotai.db import Base, LotGeoSnapshot, MapDataset, MapTile, ProcessedLot
from bankrotai.services import map_builder
from bankrotai.services.map_view import build_map_lot_detail
from bankrotai.services.map_builder import (
    _promote_map_dataset,
    build_map_dataset,
    cleanup_map_datasets,
    map_dataset_storage_statistics,
)


def _database():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        upsert_user(session, "reader", "a sufficiently secure password", role="reader")
        lot = ProcessedLot(
            external_id="tile-lot",
            source="test",
            source_system="test",
            title="Тайловый лот",
            description="",
            category="land",
            start_price=Decimal("1000000"),
            current_price=Decimal("900000"),
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
                centroid_lat=57.6261,
                centroid_lon=39.8845,
            )
        )
        session.commit()
    return factory


def test_builder_publishes_cluster_and_point_tiles_atomically(monkeypatch):
    factory = _database()
    info = Mock()
    monkeypatch.setattr(map_builder.logger, "info", info)
    result = build_map_dataset(factory)
    assert result["build_status"] == "success"
    assert result["promotion_status"] == "published"
    assert result["source_lot_count"] == 1
    assert result["point_count"] == 1
    assert result["tile_count"] > 0
    assert result["build_duration_ms"] >= 0
    assert result["duration_ms"] >= result["build_duration_ms"]
    assert result["storage"]["current_dataset_count"] == 1
    messages = [call.args[0] for call in info.call_args_list]
    assert any("build started" in message for message in messages)
    assert any("build succeeded" in message for message in messages)
    assert any("promotion succeeded" in message for message in messages)
    assert any("promotion finished" in message for message in messages)
    assert any("published" in call.args for call in info.call_args_list)
    with factory() as session:
        dataset = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
        assert dataset is not None and dataset.status == "ready"
        cluster = session.scalar(select(MapTile).where(MapTile.dataset_id == dataset.id, MapTile.z == 7))
        point = session.scalar(select(MapTile).where(MapTile.dataset_id == dataset.id, MapTile.z == 12))
        assert cluster.payload_json["features"][0]["kind"] == "cluster"
        assert cluster.payload_json["features"][0]["count"] == 1
        assert point.payload_json["features"][0]["kind"] == "lot"
        assert cluster.payload_json["yandex"]["type"] == "FeatureCollection"
        yandex_cluster = cluster.payload_json["yandex"]["features"][0]
        assert yandex_cluster["properties"]["kind"] == "cluster"
        assert yandex_cluster["properties"]["count"] == 1
        assert yandex_cluster["geometry"]["type"] == "Point"
        assert yandex_cluster["options"]["preset"] == "islands#blueCircleIcon"
        yandex_point = point.payload_json["yandex"]["features"][0]
        assert yandex_point["type"] == "Feature"
        assert yandex_point["properties"]["kind"] == "lot"
        assert yandex_point["properties"]["lotId"] == yandex_point["id"]
        assert yandex_point["geometry"]["coordinates"] == pytest.approx([57.6261, 39.8845])


def test_builder_dataset_membership_business_matrix():
    factory = _database()
    now = datetime(2026, 9, 6, 12, 0, 0)
    cases = [
        ("closed-visible", {"auction_status": "closed"}, True, "low", True),
        ("completed-visible", {"auction_status": "completed"}, True, "unknown", True),
        ("manual-review-visible", {"needs_human_review": True}, True, "medium", True),
        ("approved-visible", {"review_status": "approved"}, True, "high", True),
        ("rejected-visible", {"review_status": "rejected"}, True, "none", True),
        ("archived-hidden", {"is_archived": True}, True, "high", False),
        ("no-geo-hidden", {}, False, "high", False),
        ("invalid-geo-hidden", {}, True, "high", False),
    ]
    ids: dict[str, int] = {}
    with factory() as session:
        primary = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "tile-lot"))
        assert primary is not None
        for index, (name, overrides, has_geo, confidence, _included) in enumerate(cases):
            lot = ProcessedLot(
                external_id=name,
                source="test",
                source_system="test",
                title=name,
                description="",
                category="land",
                **{"auction_status": "active", **overrides},
            )
            session.add(lot)
            session.flush()
            ids[name] = lot.id
            if has_geo:
                invalid = name == "invalid-geo-hidden"
                session.add(
                    LotGeoSnapshot(
                        lot_id=lot.id,
                        geo_source="test",
                        geo_method="fixture",
                        geo_confidence=confidence,
                        centroid_lat=95.0 if invalid else 55.7 + index / 100,
                        centroid_lon=200.0 if invalid else 37.6 + index / 100,
                        observed_at=now + timedelta(minutes=index),
                    )
                )
        duplicate = ProcessedLot(
            external_id="duplicate-hidden",
            source="test",
            source_system="test",
            title="duplicate-hidden",
            description="",
            category="land",
            auction_status="active",
            duplicate_of_id=primary.id,
        )
        session.add(duplicate)
        session.flush()
        ids["duplicate-hidden"] = duplicate.id
        session.add(
            LotGeoSnapshot(
                lot_id=duplicate.id,
                geo_source="test",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=55.8,
                centroid_lon=37.8,
                observed_at=now,
            )
        )
        session.commit()

    build_map_dataset(factory)
    with factory() as session:
        current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
        assert current is not None
        tiles = session.scalars(select(MapTile).where(MapTile.dataset_id == current.id, MapTile.z == 12)).all()
        included_ids = {
            int(feature["id"])
            for tile in tiles
            for feature in tile.payload_json["features"]
            if feature["kind"] == "lot"
        }

    expected_included = {ids[name] for name, _overrides, _geo, _confidence, included in cases if included}
    expected_included.add(primary.id)
    assert expected_included <= included_ids
    assert ids["archived-hidden"] not in included_ids
    assert ids["no-geo-hidden"] not in included_ids
    assert ids["invalid-geo-hidden"] not in included_ids
    assert ids["duplicate-hidden"] not in included_ids


def test_builder_uses_latest_geo_by_observed_at_then_id():
    factory = _database()
    with factory() as session:
        lot = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "tile-lot"))
        assert lot is not None
        original = session.scalar(select(LotGeoSnapshot).where(LotGeoSnapshot.lot_id == lot.id))
        assert original is not None
        original.observed_at = datetime(2026, 9, 6, 12, 0, 0)
        # Inserted later means a larger id, but observed_at makes this snapshot older.
        session.add(
            LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="older-import",
                geo_method="fixture",
                geo_confidence="low",
                centroid_lat=10.0,
                centroid_lon=20.0,
                observed_at=datetime(2026, 9, 5, 12, 0, 0),
            )
        )
        # Equal observed_at is deterministically resolved by the larger id.
        session.add(
            LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="same-time-later-id",
                geo_method="fixture",
                geo_confidence="medium",
                centroid_lat=58.0,
                centroid_lon=40.0,
                observed_at=datetime(2026, 9, 6, 12, 0, 0),
            )
        )
        session.commit()

    build_map_dataset(factory)
    with factory() as session:
        current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
        assert current is not None
        tiles = session.scalars(select(MapTile).where(MapTile.dataset_id == current.id, MapTile.z == 12)).all()
        feature = next(
            feature
            for tile in tiles
            for feature in tile.payload_json["features"]
            if feature["kind"] == "lot" and feature["id"] == lot.id
        )
    assert feature["lat"] == pytest.approx(58.0)
    assert feature["lon"] == pytest.approx(40.0)
    with factory() as session:
        detail = build_map_lot_detail(session, lot.id)
    assert detail is not None
    assert detail["lat"] == pytest.approx(58.0)
    assert detail["lon"] == pytest.approx(40.0)


def test_builder_keeps_distinct_lots_at_same_coordinates_without_duplicate_ids():
    factory = _database()
    with factory() as session:
        second = ProcessedLot(
            external_id="coincident-lot",
            source="test",
            source_system="test",
            title="Совпадающая точка",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(second)
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=second.id,
                geo_source="test",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=57.6261,
                centroid_lon=39.8845,
            )
        )
        session.commit()
        second_id = second.id

    build_map_dataset(factory)
    with factory() as session:
        current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
        assert current is not None
        tiles = session.scalars(select(MapTile).where(MapTile.dataset_id == current.id, MapTile.z == 12)).all()
        ids = [
            int(feature["id"])
            for tile in tiles
            for feature in tile.payload_json["features"]
            if feature["kind"] == "lot"
        ]
    assert len(ids) == len(set(ids)) == 2
    assert second_id in ids


def test_successful_build_atomically_replaces_current_and_retains_history():
    factory = _database()
    old_result = build_map_dataset(factory)
    new_result = build_map_dataset(factory)

    with factory() as session:
        old = session.scalar(select(MapDataset).where(MapDataset.version == old_result["version"]))
        new = session.scalar(select(MapDataset).where(MapDataset.version == new_result["version"]))
        assert old is not None and old.is_current is False
        assert new is not None and new.is_current is True and new.status == "ready"
        assert session.query(MapDataset).filter_by(is_current=True).count() == 1


def test_failed_promotion_keeps_old_dataset_current(monkeypatch):
    factory = _database()
    old_result = build_map_dataset(factory)
    monkeypatch.setattr(
        map_builder,
        "_promote_map_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected build failure")),
    )
    info = Mock()
    exception = Mock()
    monkeypatch.setattr(map_builder.logger, "info", info)
    monkeypatch.setattr(map_builder.logger, "exception", exception)

    with pytest.raises(RuntimeError, match="injected build failure"):
        build_map_dataset(factory)

    assert any("build succeeded" in call.args[0] for call in info.call_args_list)
    assert "promotion failed after successful build" in exception.call_args.args[0]

    with factory() as session:
        old = session.scalar(select(MapDataset).where(MapDataset.version == old_result["version"]))
        failed = session.scalar(select(MapDataset).where(MapDataset.version != old_result["version"]))
        assert old is not None and old.is_current is True
        assert failed is not None and failed.is_current is False and failed.status == "failed"


def test_mid_build_failure_keeps_old_current_and_partial_dataset_hidden(monkeypatch):
    factory = _database()
    old_result = build_map_dataset(factory)
    engine = factory.kw["bind"]
    state = {"tile_inserts": 0}

    def fail_during_tile_writes(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO MAP_TILES"):
            state["tile_inserts"] += 1
            if state["tile_inserts"] == 4:
                raise RuntimeError("injected mid-build failure")

    event.listen(engine, "before_cursor_execute", fail_during_tile_writes)
    exception = Mock()
    monkeypatch.setattr(map_builder.logger, "exception", exception)
    try:
        with pytest.raises(RuntimeError, match="injected mid-build failure"):
            build_map_dataset(factory)
    finally:
        event.remove(engine, "before_cursor_execute", fail_during_tile_writes)

    assert "Map dataset build failed" in exception.call_args.args[0]

    with factory() as session:
        old = session.scalar(select(MapDataset).where(MapDataset.version == old_result["version"]))
        failed = session.scalar(select(MapDataset).where(MapDataset.version != old_result["version"]))
        assert old is not None and old.is_current is True
        assert failed is not None and failed.is_current is False and failed.status == "failed"
        assert session.query(MapTile).filter_by(dataset_id=failed.id).count() > 0
        storage = map_dataset_storage_statistics(session)
        assert storage["dataset_count"] == 2
        assert storage["current_dataset_count"] == 1
        assert storage["failed_dataset_count"] == 1
        assert storage["non_current_dataset_count"] == 1
        assert storage["failed_or_rejected_tile_count"] > 0


def test_empty_dataset_cannot_replace_nonempty_current(monkeypatch):
    factory = _database()
    old_result = build_map_dataset(factory)
    with factory() as session:
        session.query(ProcessedLot).update({ProcessedLot.is_archived: True})
        session.commit()

    info = Mock()
    warning = Mock()
    monkeypatch.setattr(map_builder.logger, "info", info)
    monkeypatch.setattr(map_builder.logger, "warning", warning)
    rejected = build_map_dataset(factory)

    assert rejected["build_status"] == "success"
    assert rejected["promotion_status"] == "rejected"
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "empty_dataset_would_replace_nonempty_current"
    assert any("build succeeded" in call.args[0] for call in info.call_args_list)
    assert "promotion rejected by coverage guard" in warning.call_args.args[0]
    assert any("rejected" in call.args for call in info.call_args_list)
    with factory() as session:
        old = session.scalar(select(MapDataset).where(MapDataset.version == old_result["version"]))
        new = session.scalar(select(MapDataset).where(MapDataset.version == rejected["version"]))
        assert old is not None and old.is_current is True
        assert new is not None and new.is_current is False and new.status == "rejected"


def test_empty_dataset_is_allowed_for_initial_bootstrap():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    result = build_map_dataset(factory)

    assert result["status"] == "published"
    assert result["point_count"] == result["tile_count"] == 0
    with factory() as session:
        dataset = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
        assert dataset is not None and dataset.status == "ready"


def test_relative_coverage_drop_is_rejected_without_losing_current():
    factory = _database()
    with factory() as session:
        old = MapDataset(
            version="old-coverage",
            status="ready",
            is_current=True,
            point_count=100,
            tile_count=0,
        )
        new = MapDataset(
            version="new-coverage",
            status="building",
            is_current=False,
            point_count=49,
            tile_count=0,
        )
        session.add_all([old, new])
        session.commit()
        old_id, new_id = old.id, new.id

    with factory() as session:
        failures = map_builder._dimension_coverage_failures(
            session,
            current_dataset_id=old_id,
            new_dataset_id=new_id,
            minimum_ratio=0.5,
        )
    result = _promote_map_dataset(
        factory,
        dataset_id=new_id,
        expected_current_id=old_id,
        dimension_failures=failures,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "dataset_coverage_below_threshold"
    assert result["minimum_point_count"] == 50
    with factory() as session:
        assert session.get(MapDataset, old_id).is_current is True
        rejected = session.get(MapDataset, new_id)
        assert rejected.is_current is False and rejected.status == "rejected"


def test_dimension_coverage_guard_rejects_disappearing_source():
    factory = _database()
    with factory() as session:
        old = MapDataset(version="old-dim", status="ready", is_current=True, point_count=40, tile_count=1)
        new = MapDataset(version="new-dim", status="building", is_current=False, point_count=20, tile_count=1)
        session.add_all([old, new])
        session.flush()
        lots = [
            ProcessedLot(
                source_system="lost-source" if index < 20 else "kept-source",
                source="test",
                external_id=f"dim-{index}",
                title="Lot",
                description="",
                category="land",
                auction_status="active",
                region_code="76",
            )
            for index in range(40)
        ]
        session.add_all(lots)
        session.flush()
        session.add_all(
            [
                MapTile(
                    dataset_id=old.id,
                    z=12,
                    x=1,
                    y=1,
                    feature_count=40,
                    etag="old-dim",
                    payload_json={"features": [{"kind": "lot", "id": lot.id} for lot in lots]},
                ),
                MapTile(
                    dataset_id=new.id,
                    z=12,
                    x=1,
                    y=1,
                    feature_count=20,
                    etag="new-dim",
                    payload_json={"features": [{"kind": "lot", "id": lot.id} for lot in lots[20:]]},
                ),
            ]
        )
        session.commit()
        old_id, new_id = old.id, new.id

    with factory() as session:
        failures = map_builder._dimension_coverage_failures(
            session,
            current_dataset_id=old_id,
            new_dataset_id=new_id,
            minimum_ratio=0.5,
        )
    result = _promote_map_dataset(
        factory,
        dataset_id=new_id,
        expected_current_id=old_id,
        dimension_failures=failures,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "dataset_dimension_coverage_below_threshold"
    assert any(
        failure["dimension"] == "source" and failure["value"] == "lost-source"
        for failure in result["coverage_failures"]
    )
    with factory() as session:
        assert session.get(MapDataset, old_id).is_current is True
        assert session.get(MapDataset, new_id).is_current is False


def test_promotion_refuses_incomplete_tile_metadata():
    factory = _database()
    old_result = build_map_dataset(factory)
    with factory() as session:
        old_id = session.scalar(select(MapDataset.id).where(MapDataset.version == old_result["version"]))
        incomplete = MapDataset(
            version="incomplete",
            status="building",
            is_current=False,
            point_count=1,
            tile_count=1,
        )
        session.add(incomplete)
        session.commit()
        incomplete_id = incomplete.id

    with pytest.raises(RuntimeError, match="actual_tiles=0"):
        _promote_map_dataset(
            factory,
            dataset_id=incomplete_id,
            expected_current_id=old_id,
        )

    with factory() as session:
        assert session.get(MapDataset, old_id).is_current is True
        assert session.get(MapDataset, incomplete_id).is_current is False


def test_database_rejects_a_second_current_dataset():
    factory = _database()
    with factory() as session:
        session.add(MapDataset(version="current-a", status="ready", is_current=True))
        session.commit()
        session.add(MapDataset(version="current-b", status="ready", is_current=True))
        with pytest.raises(IntegrityError):
            session.commit()


def test_legacy_tile_api_remains_private_while_yandex_tiles_are_public_immutable(monkeypatch):
    factory = _database()
    result = build_map_dataset(factory)
    with factory() as session:
        session.add(
            MapDataset(
                version="partial-building-version",
                status="building",
                is_current=False,
                point_count=999,
                tile_count=999,
            )
        )
        session.add_all(
            [
                MapDataset(version="failed-version", status="failed", is_current=False),
                MapDataset(version="rejected-version", status="rejected", is_current=False),
                MapDataset(version="unpublished-ready-version", status="ready", is_current=False),
            ]
        )
        session.commit()

    @contextmanager
    def scope():
        with factory() as session:
            yield session

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(api.settings, "public_api_key", "service-key-that-is-long-enough")
    monkeypatch.setattr(api.settings, "auth_session_secret", "session-secret-" * 4)
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)
    unauthorized = TestClient(api.app, base_url="https://testserver")
    assert unauthorized.get("/api/map/datasets/current").status_code == 401
    assert unauthorized.get(f"/api/map/tiles/{result['version']}/0/0/0").status_code == 401
    assert unauthorized.get(f"/api/map/yandex-tiles/{result['version']}/0/0/0").status_code == 401
    client = TestClient(api.app, base_url="https://testserver", headers={"X-API-Key": api.settings.public_api_key})
    assert (
        client.post(
            "/api/auth/login",
            json={
                "username": "reader",
                "password": "a sufficiently secure password",
            },
        ).status_code
        == 200
    )
    current = client.get("/api/map/datasets/current")
    assert current.status_code == 200
    assert current.json()["version"] == result["version"]
    assert current.headers["cache-control"] == "private, max-age=5, stale-while-revalidate=30"
    assert current.headers["etag"] == f'"dataset-{result["version"]}"'
    assert current.json()["bootstrap_zoom"] == 7
    assert current.json()["bootstrap_center"] == pytest.approx([57.6261, 39.8845])
    assert len(current.json()["bootstrap_tiles"]) == 9
    for bootstrap in current.json()["bootstrap_tiles"]:
        assert bootstrap["payload"]["type"] == "FeatureCollection"
        assert isinstance(bootstrap["payload"]["features"], list)
    assert current.headers["x-map-dataset"] == result["version"]
    current_not_modified = client.get(
        "/api/map/datasets/current",
        headers={"If-None-Match": current.headers["etag"]},
    )
    assert current_not_modified.status_code == 304
    assert current_not_modified.headers["x-map-dataset"] == result["version"]
    for hidden_version in (
        "partial-building-version",
        "failed-version",
        "rejected-version",
        "unpublished-ready-version",
        "missing-version",
    ):
        assert client.get(f"/api/map/tiles/{hidden_version}/0/0/0").status_code == 404
    with factory() as session:
        tile = session.scalar(select(MapTile).join(MapDataset).where(MapDataset.version == result["version"]))
    response = client.get(f"/api/map/tiles/{result['version']}/{tile.z}/{tile.x}/{tile.y}")
    assert response.status_code == 200
    assert "private" in response.headers["cache-control"]
    assert "immutable" in response.headers["cache-control"]
    assert response.headers["etag"]
    assert response.headers["x-map-dataset"] == result["version"]
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["features"]
    allowed_cluster = {"kind", "id", "lat", "lon", "count", "bounds"}
    allowed_lot = {
        "kind",
        "id",
        "lat",
        "lon",
        "title",
        "current_price",
        "start_price",
        "status",
        "review_status",
    }
    forbidden = {"password", "password_hash", "notes", "user_id", "raw_data", "filesystem_path", "debug"}
    for feature in response.json()["features"]:
        assert set(feature) <= (allowed_cluster if feature["kind"] == "cluster" else allowed_lot)
        assert not (set(feature) & forbidden)
    not_modified = client.get(
        f"/api/map/tiles/{result['version']}/{tile.z}/{tile.x}/{tile.y}",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == response.headers["etag"]

    yandex_response = client.get(
        f"/api/map/yandex-tiles/{result['version']}/{tile.z}/{tile.x}/{tile.y}"
    )
    assert yandex_response.status_code == 200
    assert yandex_response.headers["cache-control"] == (
        "private, max-age=31536000, immutable"
    )
    assert "Accept-Encoding" in yandex_response.headers["vary"]
    assert yandex_response.headers["x-map-dataset"] == result["version"]
    yandex_payload = yandex_response.json()
    assert yandex_payload["type"] == "FeatureCollection"
    assert yandex_payload["features"]
    yandex_feature = yandex_payload["features"][0]
    assert yandex_feature["type"] == "Feature"
    assert yandex_feature["geometry"]["type"] == "Point"
    assert yandex_feature["properties"]["kind"] in {"cluster", "lot"}
    assert not (set(yandex_feature.get("properties", {})) & forbidden)

    yandex_not_modified = client.get(
        f"/api/map/yandex-tiles/{result['version']}/{tile.z}/{tile.x}/{tile.y}",
        headers={"If-None-Match": yandex_response.headers["etag"]},
    )
    assert yandex_not_modified.status_code == 304

    empty_yandex = client.get(
        f"/api/map/yandex-tiles/{result['version']}/14/16383/16383"
    )
    assert empty_yandex.status_code == 200
    assert empty_yandex.json() == {"type": "FeatureCollection", "features": []}

    for z, x, y in ((-1, 0, 0), (15, 0, 0), (0, -1, 0), (0, 0, -1), (0, 1, 0), (0, 0, 1)):
        assert client.get(f"/api/map/tiles/{result['version']}/{z}/{x}/{y}").status_code == 404
        assert client.get(f"/api/map/yandex-tiles/{result['version']}/{z}/{x}/{y}").status_code == 404
    assert client.get(f"/api/map/tiles/{result['version']}/0/0/0").status_code == 200
    assert client.get(f"/api/map/tiles/{result['version']}/14/16383/16383").status_code == 200
    assert unauthorized.get("/api/map/lots/1").status_code == 401


def test_current_dataset_api_hides_unpublished_states(monkeypatch):
    factory = _database()
    with factory() as session:
        session.add(MapDataset(version="building-current", status="building", is_current=True))
        session.commit()

    @contextmanager
    def scope():
        with factory() as session:
            yield session

    monkeypatch.setattr(api, "read_session_scope", scope)
    client = TestClient(api.app)
    assert client.get("/api/map/datasets/current").status_code == 404


def test_map_dataset_cleanup_is_dry_run_and_preserves_current_and_rollback(monkeypatch):
    factory = _database()
    current_result = build_map_dataset(factory)
    now = datetime(2026, 9, 20, 12, 0, 0)
    old = now - timedelta(days=30)
    with factory() as session:
        current = session.scalar(select(MapDataset).where(MapDataset.version == current_result["version"]))
        assert current is not None
        current.created_at = now
        previous = MapDataset(
            version="previous-ready",
            status="ready",
            is_current=False,
            point_count=1,
            tile_count=1,
            created_at=old,
            published_at=old,
        )
        older = MapDataset(
            version="older-ready",
            status="ready",
            is_current=False,
            point_count=1,
            tile_count=1,
            created_at=old - timedelta(days=1),
            published_at=old - timedelta(days=1),
        )
        failed = MapDataset(version="old-failed", status="failed", is_current=False, created_at=old)
        recent_failed = MapDataset(
            version="recent-failed",
            status="failed",
            is_current=False,
            created_at=now - timedelta(hours=1),
        )
        session.add_all([previous, older, failed, recent_failed])
        session.flush()
        for dataset in (previous, older, failed):
            session.add(
                MapTile(
                    dataset_id=dataset.id,
                    z=0,
                    x=0,
                    y=0,
                    feature_count=0,
                    etag=f"etag-{dataset.id}",
                    payload_json={"features": []},
                )
            )
        session.commit()

    dry_run = cleanup_map_datasets(factory, now=now)
    assert dry_run == {
        "dry_run": True,
        "retained_previous_ready": 1,
        "candidate_dataset_count": 2,
        "candidate_tile_count": 2,
        "candidate_versions": ["older-ready", "old-failed"],
    }
    with factory() as session:
        assert session.query(MapDataset).count() == 5

    applied = cleanup_map_datasets(factory, now=now, apply=True)
    assert applied["dry_run"] is False
    with factory() as session:
        versions = set(session.scalars(select(MapDataset.version)))
        assert current_result["version"] in versions
        assert "previous-ready" in versions
        assert "recent-failed" in versions
        assert "older-ready" not in versions
        assert "old-failed" not in versions
        assert (
            session.scalar(select(func.count(MapTile.id)).where(MapTile.dataset_id.not_in(select(MapDataset.id)))) == 0
        )

    @contextmanager
    def scope():
        with factory() as session:
            yield session

    monkeypatch.setattr(api, "read_session_scope", scope)
    response = TestClient(api.app).get("/api/map/datasets/current")
    assert response.status_code == 200
    assert response.json()["version"] == current_result["version"]
