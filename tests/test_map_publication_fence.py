"""Publication-fence regression tests; disposable SQLite only, no production writes."""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, BackgroundTaskState, LotSyncRun, MapDataset
from bankrotai.services import map_builder


def _db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add_all([
            MapDataset(version="old", is_current=True, status="ready",
                       point_count=0, tile_count=0),
            MapDataset(version="new", is_current=False, status="building",
                       point_count=0, tile_count=0),
        ])
        s.commit()
    return factory


def _ids(factory):
    with factory() as s:
        return (
            s.scalar(select(MapDataset.id).where(MapDataset.version == "old")),
            s.scalar(select(MapDataset.id).where(MapDataset.version == "new")),
        )


def _assert_old_preserved(factory):
    with factory() as s:
        assert s.scalar(select(MapDataset.version).where(MapDataset.is_current.is_(True))) == "old"


def test_concurrent_write_rejects_candidate_and_preserves_old(monkeypatch):
    factory = _db()
    old, new = _ids(factory)
    monkeypatch.setattr(map_builder, "map_source_fingerprint", lambda s: "changed")
    result = map_builder._promote_map_dataset(
        factory, dataset_id=new, expected_current_id=old,
        expected_source_fingerprint="original",
    )
    assert result["reason"] == "source_changed_during_build"
    _assert_old_preserved(factory)


def test_geo_queue_blocks_promotion_then_rerun_succeeds(monkeypatch):
    factory = _db()
    old, new = _ids(factory)
    monkeypatch.setattr(map_builder, "map_source_fingerprint", lambda s: "stable")
    with factory() as s:
        s.add(BackgroundTaskState(
            task_id="geo-test", task_type="geocoding", status="running"
        ))
        s.commit()
    blocked = map_builder._promote_map_dataset(
        factory, dataset_id=new, expected_current_id=old,
        expected_source_fingerprint="stable",
    )
    assert blocked["reason"] == "upstream_work_in_progress"
    assert "geo_in_progress" in blocked["blockers"]
    _assert_old_preserved(factory)
    # A rejected candidate is immutable: retry with a freshly staged dataset.
    with factory() as s:
        s.scalar(select(BackgroundTaskState).where(
            BackgroundTaskState.task_id == "geo-test")).status = "completed"
        s.add(MapDataset(version="retry", status="building", is_current=False))
        s.commit()
        retry_id = s.scalar(select(MapDataset.id).where(MapDataset.version == "retry"))
    result = map_builder._promote_map_dataset(
        factory, dataset_id=retry_id, expected_current_id=old,
        expected_source_fingerprint="stable",
    )
    assert result["status"] == "published"
    with factory() as s:
        assert s.scalar(select(MapDataset.version).where(MapDataset.is_current.is_(True))) == "retry"


def test_failed_geo_job_is_not_mistaken_for_running_job():
    factory = _db()
    with factory() as s:
        s.add(BackgroundTaskState(
            task_id="geo-failed", task_type="geocoding", status="failed"
        ))
        s.commit()
        # Prior failures need operational review but are not active writes.
        assert map_builder.map_publication_blockers(s) == []


def test_active_ingestion_blocks_publication():
    factory = _db()
    with factory() as s:
        s.add(LotSyncRun(id="active", trigger_type="manual", status="running"))
        s.commit()
        assert "ingestion_in_progress" in map_builder.map_publication_blockers(s)
