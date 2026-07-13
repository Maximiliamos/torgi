from __future__ import annotations

from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session

from bankrotai import core, db
from bankrotai.db import Base, LotGeoSnapshot, LotStatusHistory, ProcessedLot, ValuationRun
from bankrotai.logic import apply_lot_status, cleanup_closed_lots, delete_lot


ROOT = Path(__file__).resolve().parents[1]


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _lot(**overrides) -> ProcessedLot:
    values = {
        "external_id": "lot-1",
        "source": "test",
        "source_system": "test",
        "title": "Lot",
        "description": "Description",
        "category": "land",
        "region_slug": "yaroslavl",
        "auction_status": "active",
    }
    values.update(overrides)
    return ProcessedLot(**values)


def test_closed_lot_is_archived_and_related_data_is_preserved() -> None:
    with Session(_engine()) as session:
        lot = _lot(auction_status="closed")
        session.add(lot)
        session.flush()
        session.add(ValuationRun(lot_id=lot.id, valuation_method="test"))
        session.add(LotGeoSnapshot(
            lot_id=lot.id,
            geo_source="test",
            geo_method="address",
            geo_confidence="high",
            centroid_lat=57.6,
            centroid_lon=39.8,
        ))
        session.flush()

        assert cleanup_closed_lots(session) == 1
        assert session.get(ProcessedLot, lot.id) is lot
        assert lot.is_archived is True
        assert lot.archived_at is not None
        assert lot.closed_at is not None
        assert session.scalar(select(ValuationRun).where(ValuationRun.lot_id == lot.id)) is not None
        assert session.scalar(select(LotGeoSnapshot).where(LotGeoSnapshot.lot_id == lot.id)) is not None
        assert cleanup_closed_lots(session) == 0


def test_status_history_records_only_real_transitions() -> None:
    with Session(_engine()) as session:
        lot = _lot()
        session.add(lot)
        session.flush()
        assert apply_lot_status(session, lot, "closed", "sync") is True
        assert apply_lot_status(session, lot, "closed", "sync") is False
        session.flush()
        history = session.scalars(select(LotStatusHistory).where(LotStatusHistory.lot_id == lot.id)).all()
        assert [(row.old_status, row.new_status, row.source) for row in history] == [
            ("active", "closed", "sync")
        ]


def test_manual_delete_uses_foreign_key_cascade_without_pragma_in_business_logic() -> None:
    with Session(_engine()) as session:
        lot = _lot()
        session.add(lot)
        session.flush()
        session.add(LotStatusHistory(lot_id=lot.id, old_status=None, new_status="active", source="import"))
        session.flush()
        assert delete_lot(session, lot.id) is True
        assert session.get(ProcessedLot, lot.id) is None
        assert session.scalar(select(LotStatusHistory).where(LotStatusHistory.lot_id == lot.id)) is None


def _upgrade_database(path: Path) -> None:
    core._settings_cache = core.AppSettings(database_url=f"sqlite:///{path.as_posix()}")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


def test_clean_database_migrates_to_head_with_archive_and_cadastral_schema(tmp_path: Path) -> None:
    path = tmp_path / "clean.db"
    _upgrade_database(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    schema = inspect(engine)
    columns = {column["name"] for column in schema.get_columns("processed_lots")}
    assert {"cadastral_numbers", "is_archived", "archived_at", "closed_at"} <= columns
    assert "lot_status_history" in schema.get_table_names()


def test_frozen_initialization_runs_alembic_migrations(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "frozen.db"
    core._settings_cache = core.AppSettings(database_url=f"sqlite:///{path.as_posix()}")
    db.get_engine.cache_clear()
    db.SessionLocal.configure(bind=db.get_engine())
    monkeypatch.setattr(db, "REPO_ROOT", ROOT)
    monkeypatch.setattr(db.sys, "frozen", True, raising=False)
    monkeypatch.setattr(db, "_SCHEMA_READY", False)
    db.init_db()
    with db.get_engine().connect() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == ScriptDirectory.from_config(config).get_current_head()
    db.get_engine().dispose()
    db.get_engine.cache_clear()
    core._settings_cache = None
