from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from bankrotai import core
from bankrotai.db import LotStatusHistory, ProcessedLot
from bankrotai.logic import apply_lot_status, build_lots_response, cleanup_closed_lots


pytestmark = pytest.mark.postgres
DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def engine():
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    core._settings_cache = core.AppSettings(database_url=DATABASE_URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    yield engine
    engine.dispose()
    core._settings_cache = None


def _lot(external_id: str, **overrides) -> ProcessedLot:
    values = dict(
        external_id=external_id, source="test", source_system="test", title="Склад",
        description="Площадка", object_name="Объект", address="Ярославль",
        cadastral_numbers=["76:23:010101:1"], category="land", region_slug="76",
        current_price=100, auction_status="active",
    )
    values.update(overrides)
    return ProcessedLot(**values)


def test_postgres_migrations_json_archive_history_and_search(engine) -> None:
    schema = inspect(engine)
    assert "lot_status_history" in schema.get_table_names()
    assert "cadastral_numbers" in {item["name"] for item in schema.get_columns("processed_lots")}
    with Session(engine) as session:
        lot = _lot("pg-lot")
        session.add(lot)
        session.flush()
        assert build_lots_response(session, "yaroslavl", search="010101:1")["total"] == 1
        assert apply_lot_status(session, lot, "closed", "sync")
        assert cleanup_closed_lots(session) == 0  # status transition already archived it
        session.commit()
        assert build_lots_response(session, "yaroslavl", search="010101:1")["total"] == 0
        assert session.scalar(select(LotStatusHistory).where(LotStatusHistory.lot_id == lot.id)) is not None
        assert lot.is_archived is True


def test_postgres_external_id_is_unique_per_source(engine) -> None:
    with Session(engine) as session:
        session.add(_lot("stable-external-id", source="first", source_system="first"))
        session.add(_lot("stable-external-id", source="second", source_system="second"))
        session.commit()
    with Session(engine) as session:
        assert session.query(ProcessedLot).filter_by(external_id="stable-external-id").count() == 2
