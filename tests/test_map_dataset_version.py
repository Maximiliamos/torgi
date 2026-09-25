from bankrotai.services.map_dataset_version import (
    MAP_DATASET_REVISION,
    build_map_dataset_version,
    dataset_matches_current_pipeline,
    dataset_pipeline_revision,
)


def test_dataset_versions_carry_current_pipeline_revision():
    timestamp = "20260925T120000000000Z"

    bundled = build_map_dataset_version(
        timestamp,
        object_store_enabled=True,
        object_store_layout="regional-bundles-v1",
    )
    tiled = build_map_dataset_version(
        timestamp,
        object_store_enabled=True,
        object_store_layout="tiles",
    )
    local = build_map_dataset_version(
        timestamp,
        object_store_enabled=False,
        object_store_layout="regional-bundles-v1",
    )

    assert bundled == f"{timestamp}-{MAP_DATASET_REVISION}-bundle-s3"
    assert tiled == f"{timestamp}-{MAP_DATASET_REVISION}-s3"
    assert local == f"{timestamp}-{MAP_DATASET_REVISION}"
    assert dataset_pipeline_revision(bundled) == MAP_DATASET_REVISION
    assert dataset_pipeline_revision(tiled) == MAP_DATASET_REVISION
    assert dataset_pipeline_revision(local) == MAP_DATASET_REVISION


def test_legacy_dataset_never_matches_current_pipeline():
    assert dataset_pipeline_revision("20260925T100247959809Z-bundle-s3") is None
    assert not dataset_matches_current_pipeline(
        "20260925T100247959809Z-bundle-s3",
        object_store_enabled=True,
        object_store_layout="regional-bundles-v1",
    )


def test_current_dataset_must_match_layout_and_revision():
    revision = MAP_DATASET_REVISION

    assert dataset_matches_current_pipeline(
        f"20260925T120000000000Z-{revision}-bundle-s3",
        object_store_enabled=True,
        object_store_layout="regional-bundles-v1",
    )
    assert not dataset_matches_current_pipeline(
        f"20260925T120000000000Z-{revision}-s3",
        object_store_enabled=True,
        object_store_layout="regional-bundles-v1",
    )
    assert dataset_matches_current_pipeline(
        f"20260925T120000000000Z-{revision}-s3",
        object_store_enabled=True,
        object_store_layout="tiles",
    )
