from __future__ import annotations

from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.core import AppSettings
from bankrotai.db import Base, MapDataset, MapTile
from bankrotai.services import map_bundle_store
from bankrotai.services.map_bundle_store import (
    CFO_REGION_CODES,
    REGIONAL_BUNDLE_LAYOUT,
    _bundle_bucket,
    _bundle_object_key,
    _tile_region_code,
    normalize_map_region_code,
    publish_dataset_to_regional_bundles,
)


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _lot_payload(lot_id: int, region: str, lat: float, lon: float):
    return {
        "yandex": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": lot_id,
                    "geometry": {"type": "Point", "coordinates": [lat, lon]},
                    "properties": {
                        "kind": "lot",
                        "lotId": lot_id,
                        "title": f"Lot {lot_id}",
                        "region_code": region,
                        "status": "active",
                        "review_status": "approved",
                    },
                    "options": {"preset": "islands#greenDotIcon"},
                }
            ],
        }
    }


def _cluster_payload():
    return {
        "yandex": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "c:7:1:1",
                    "geometry": {"type": "Point", "coordinates": [57.6, 39.8]},
                    "properties": {
                        "kind": "cluster",
                        "count": 2,
                        "bounds": [39.0, 57.0, 40.0, 58.0],
                    },
                    "options": {"preset": "islands#blueCircleIcon"},
                }
            ],
        }
    }


def _seed_bundle_dataset(factory):
    with factory() as session:
        dataset = MapDataset(
            version="bundle-v1-s3",
            status="building",
            is_current=False,
            point_count=2,
            tile_count=3,
        )
        session.add(dataset)
        session.flush()
        session.add_all(
            [
                MapTile(
                    dataset_id=dataset.id,
                    z=12,
                    x=2500,
                    y=1200,
                    feature_count=1,
                    etag="a",
                    payload_json=_lot_payload(1, "76", 57.6, 39.8),
                ),
                # Same z9 parent + same region: must share one physical bundle.
                MapTile(
                    dataset_id=dataset.id,
                    z=12,
                    x=2501,
                    y=1201,
                    feature_count=1,
                    etag="b",
                    payload_json=_lot_payload(2, "76", 57.7, 39.9),
                ),
                MapTile(
                    dataset_id=dataset.id,
                    z=7,
                    x=78,
                    y=39,
                    feature_count=1,
                    etag="c",
                    payload_json=_cluster_payload(),
                ),
            ]
        )
        session.commit()
        return dataset.id


def _settings():
    return AppSettings(
        map_object_store_enabled=True,
        map_object_store_endpoint="https://s3.regru.cloud",
        map_object_store_bucket="sterdez-map",
        map_object_store_public_base_url="https://s3.regru.cloud/sterdez-map",
        map_object_store_access_key="ACCESS",
        map_object_store_secret_key="SECRET",
        map_object_store_region="ru-1",
        map_object_store_layout=REGIONAL_BUNDLE_LAYOUT,
        map_object_store_workers=4,
        map_object_store_timeout_seconds=60,
    )


def test_cadastral_prefix_is_preferred_over_source_region_alias():
    assert normalize_map_region_code("84", "76:15:010101:55") == "76"
    assert normalize_map_region_code(None, "4:01:0004041:1") == "04"
    assert normalize_map_region_code("76", None) == "76"


def test_cfo_priority_contains_all_18_subject_codes():
    assert len(CFO_REGION_CODES) == 18
    assert {"31", "50", "76", "77"}.issubset(CFO_REGION_CODES)


def test_detail_tiles_are_grouped_by_region_and_stable_parent_cell():
    payload = {
        "type": "FeatureCollection",
        "features": _lot_payload(1, "76", 57.6, 39.8)["yandex"]["features"],
    }
    assert _tile_region_code(12, payload) == "76"
    assert _bundle_bucket(12, 2500, 1200, "76") == _bundle_bucket(12, 2501, 1201, "76")
    assert _tile_region_code(7, _cluster_payload()["yandex"]) == "_overview"


def test_bundle_object_key_is_content_addressed():
    first = _bundle_object_key(b'{"tiles":{"12/1/1":{}}}')
    second = _bundle_object_key(b'{"tiles":{"12/1/1":{}}}')
    changed = _bundle_object_key(b'{"tiles":{"12/1/2":{}}}')
    assert first == second
    assert first != changed
    assert first.startswith("bundles/v1/")


def test_regional_publisher_collapses_microtiles_and_reuses_existing_bundles(monkeypatch):
    factory = _factory()
    dataset_id = _seed_bundle_dataset(factory)
    settings = _settings()
    puts: list[tuple[str, bytes]] = []

    monkeypatch.setattr(map_bundle_store, "get_settings", lambda: settings)
    monkeypatch.setattr(map_bundle_store, "_public_object_exists", lambda _settings, _key: False)
    monkeypatch.setattr(
        map_bundle_store,
        "_put_object",
        lambda _settings, key, body, **_kwargs: puts.append((key, body)),
    )
    monkeypatch.setattr(
        map_bundle_store,
        "_verify_public_manifest",
        lambda _settings, version: {"version": version, "layout": REGIONAL_BUNDLE_LAYOUT},
    )

    result = publish_dataset_to_regional_bundles(
        factory,
        dataset_id=dataset_id,
        version="bundle-v1-s3",
    )

    assert result["bundle_count"] == 2
    assert result["uploaded_bundle_count"] == 2
    assert result["reused_bundle_count"] == 0
    bundle_keys = [key for key, _body in puts if key.startswith("bundles/v1/")]
    assert len(bundle_keys) == 2
    assert any(key.endswith("/indexes/12.json") for key, _body in puts)
    assert puts[-1][0] == "datasets/bundle-v1-s3/manifest.json"
    assert result["regions"]["76"]["point_count"] == 2
    assert result["regions"]["76"]["priority"] is True

    puts.clear()
    monkeypatch.setattr(map_bundle_store, "_public_object_exists", lambda _settings, _key: True)
    reused = publish_dataset_to_regional_bundles(
        factory,
        dataset_id=dataset_id,
        version="bundle-v1-s3",
    )

    assert reused["bundle_count"] == 2
    assert reused["uploaded_bundle_count"] == 0
    assert reused["reused_bundle_count"] == 2
    assert not any(key.startswith("bundles/v1/") for key, _body in puts)
    assert any(key.endswith("/indexes/12.json") for key, _body in puts)
    assert puts[-1][0] == "datasets/bundle-v1-s3/manifest.json"
