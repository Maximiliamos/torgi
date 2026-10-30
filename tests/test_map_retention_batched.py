"""Regression checks for bounded, resumable map retention.

Only a disposable SQLite database is modified; Home production is not touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, MapDataset, MapTile
from bankrotai.services.map_builder import cleanup_map_datasets


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def test_retention_is_bounded_restartable_and_preserves_live_map():
    engine, factory = _factory()
    now = datetime(2026, 10, 28, 12)
    old = now - timedelta(days=10)
    with factory() as session:
        current = MapDataset(
            version="current", is_current=True, status="ready",
            created_at=now, published_at=now, tile_count=1,
        )
        rollback = MapDataset(
            version="rollback", is_current=False, status="ready",
            created_at=old, published_at=old, tile_count=1,
        )
        expired = MapDataset(
            version="failed-old", is_current=False, status="failed",
            created_at=old, tile_count=9,
        )
        session.add_all([current, rollback, expired])
        session.flush()
        for dataset, count in ((current, 1), (rollback, 1), (expired, 9)):
            for x in range(count):
                session.add(MapTile(
                    dataset_id=dataset.id, z=12, x=x, y=0,
                    feature_count=0, etag=f"{dataset.id}-{x}",
                    payload_json={"features": []},
                ))
        session.commit()

    deleted_per_statement = []

    def record_statement(_conn, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith("DELETE FROM MAP_TILES"):
            deleted_per_statement.append(_cursor.rowcount)

    event.listen(engine, "after_cursor_execute", record_statement)

    first = cleanup_map_datasets(
        factory, now=now, apply=True, tile_batch_size=2, max_tiles_per_run=5
    )
    assert first["deleted_tile_count"] == 5
    assert first["deleted_dataset_count"] == 0
    assert first["deleted_versions"] == []
    assert first["candidate_versions"] == ["failed-old"]
    assert first["tile_budget_exhausted"] is True
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MapTile)) == 6
        assert session.scalar(select(MapDataset.id).where(MapDataset.version == "failed-old")) is not None

    second = cleanup_map_datasets(
        factory, now=now, apply=True, tile_batch_size=2, max_tiles_per_run=10
    )
    assert second["deleted_tile_count"] == 4
    assert second["deleted_dataset_count"] == 1
    assert second["deleted_versions"] == ["failed-old"]
    assert second["tile_budget_exhausted"] is False
    assert deleted_per_statement and max(deleted_per_statement) <= 2

    with factory() as session:
        versions = set(session.scalars(select(MapDataset.version)))
        assert versions == {"current", "rollback"}
        assert session.scalar(select(func.count()).select_from(MapTile)) == 2

    third = cleanup_map_datasets(factory, now=now, apply=True)
    assert third["candidate_dataset_count"] == 0


@pytest.mark.parametrize(
    ("tile_batch_size", "max_tiles_per_run"),
    [(0, 100), (5001, 100), (10, 0)],
)
def test_retention_rejects_unsafe_work_budget(tile_batch_size, max_tiles_per_run):
    _, factory = _factory()
    with pytest.raises(ValueError):
        cleanup_map_datasets(
            factory,
            apply=True,
            tile_batch_size=tile_batch_size,
            max_tiles_per_run=max_tiles_per_run,
        )
