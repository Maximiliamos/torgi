from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.core import AppSettings
from bankrotai.db import Base, MapDataset, MapTile
from bankrotai.services import map_bundle_store
from bankrotai.services.map_dataset_version import MAP_DATASET_REVISION
from bankrotai.services.map_bundle_store import (
    CFO_REGION_CODES,
    REGIONAL_BUNDLE_LAYOUT,
    _bundle_bucket,
    _bundle_object_key,
    _index_object_key,
    _index_shard,
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


def _seed_bundle_dataset(factory, version: str = "bundle-v1-r2-bundle-s3"):
    with factory() as session:
        dataset = MapDataset(
            version=version,
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
                # Same z8 parent + same region: must share one physical bundle.
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


def test_bundle_and_index_keys_are_content_addressed():
    first = _bundle_object_key(b'{"tiles":{"12/1/1":{}}}')
    second = _bundle_object_key(b'{"tiles":{"12/1/1":{}}}')
    changed = _bundle_object_key(b'{"tiles":{"12/1/2":{}}}')
    assert first == second
    assert first != changed
    assert first.startswith("bundles/v1/")
    index_first = _index_object_key(b'{"tiles":{"12/1/1":{"bundle":"a"}}}')
    index_second = _index_object_key(b'{"tiles":{"12/1/1":{"bundle":"a"}}}')
    assert index_first == index_second
    assert index_first.startswith("indexes/v1/")
    assert _index_shard(5, 1, 1) == "overview/root"
    assert _index_shard(7, 78, 39) == "overview/6/39/19"
    assert _index_shard(12, 2500, 1200) == "detail/8/156/75"


def test_regional_publisher_collapses_microtiles_and_reuses_existing_bundles(monkeypatch):
    factory = _factory()
    first_version = "bundle-v1-r2-bundle-s3"
    first_id = _seed_bundle_dataset(factory, first_version)
    settings = _settings()
    puts: list[tuple[str, bytes]] = []

    monkeypatch.setattr(map_bundle_store, "get_settings", lambda: settings)
    monkeypatch.setattr(
        map_bundle_store,
        "_put_object",
        lambda _settings, key, body, **_kwargs: puts.append((key, body)),
    )

    verified_manifests: dict[str, dict] = {}

    def verify(_settings, version):
        return verified_manifests.get(
            version,
            {
                "version": version,
                "layout": REGIONAL_BUNDLE_LAYOUT,
                "pipeline_revision": MAP_DATASET_REVISION,
            },
        )

    monkeypatch.setattr(map_bundle_store, "_verify_public_manifest", verify)

    first = publish_dataset_to_regional_bundles(
        factory,
        dataset_id=first_id,
        version=first_version,
    )

    assert first["bundle_count"] == 2
    assert first["uploaded_bundle_count"] == 2
    assert first["reused_bundle_count"] == 0
    assert first["index_shard_count"] == 2
    assert first["uploaded_index_count"] == 2
    assert first["reused_index_count"] == 0
    bundle_keys = [key for key, _body in puts if key.startswith("bundles/v1/")]
    index_keys = [key for key, _body in puts if key.startswith("indexes/v1/")]
    assert len(bundle_keys) == 2
    assert len(index_keys) == 2
    assert puts[-1][0] == f"datasets/{first_version}/manifest.json"
    first_manifest = __import__("json").loads(puts[-1][1])
    assert first_manifest["pipeline_revision"] == MAP_DATASET_REVISION
    assert set(first_manifest["bundle_objects"]) == set(bundle_keys)
    assert set(first_manifest["index_shards"].values()) == set(index_keys)
    assert first["regions"]["76"]["point_count"] == 2
    assert first["regions"]["76"]["priority"] is True

    verified_manifests[first_version] = first_manifest
    with factory() as session:
        previous = session.get(MapDataset, first_id)
        assert previous is not None
        previous.status = "ready"
        previous.is_current = True
        session.commit()

    second_version = "bundle-v2-r2-bundle-s3"
    second_id = _seed_bundle_dataset(factory, second_version)
    puts.clear()

    second = publish_dataset_to_regional_bundles(
        factory,
        dataset_id=second_id,
        version=second_version,
    )

    assert second["bundle_count"] == 2
    assert second["uploaded_bundle_count"] == 0
    assert second["reused_bundle_count"] == 2
    assert second["uploaded_index_count"] == 0
    assert second["reused_index_count"] == 2
    assert len(puts) == 1
    assert puts[0][0] == f"datasets/{second_version}/manifest.json"
