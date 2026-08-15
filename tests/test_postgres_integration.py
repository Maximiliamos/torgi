from __future__ import annotations

import os
from pathlib import Path
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from bankrotai import api, core
from bankrotai.db import (
    SCHEMA_REVISION,
    CanonicalLot,
    LotGeoSnapshot,
    LotStatusHistory,
    ProcessedLot,
    SourceLot,
)
from bankrotai.logic import apply_lot_status, build_lots_response, cleanup_closed_lots
from bankrotai.services.map_view import build_map_lots_response


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


def test_postgres_schema_head_indexes_and_constraints(engine) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    migration_head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        deployed_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert migration_head == SCHEMA_REVISION == deployed_revision

    schema = inspect(engine)
    processed_indexes = {item["name"] for item in schema.get_indexes("processed_lots")}
    geo_indexes = {item["name"] for item in schema.get_indexes("lot_geo_snapshots")}
    processed_unique = {item["name"] for item in schema.get_unique_constraints("processed_lots")}
    assert {
        "ix_processed_lots_region_slug",
        "ix_processed_lots_source_system",
        "ix_processed_lots_is_archived",
        "ix_processed_lots_map_feed",
    } <= processed_indexes
    assert "ix_lot_geo_snapshots_viewport" in geo_indexes
    assert "uq_processed_lots_source_system_external_id" in processed_unique


def test_postgres_ready_map_and_explain_analyze(engine, monkeypatch) -> None:
    with Session(engine) as session:
        lot = _lot("pg-map-lot", title="Mapped PostgreSQL lot", region_slug="76")
        session.add(lot)
        session.flush()
        session.add(LotGeoSnapshot(
            lot_id=lot.id,
            geo_source="ci",
            geo_method="fixture",
            geo_confidence="high",
            centroid_lat=57.6261,
            centroid_lon=39.8845,
        ))
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug="76",
            include_archived=False,
            limit=25,
            west=39,
            south=57,
            east=40,
            north=58,
        )
        assert any(item["id"] == lot.id for item in response["items"])
        plan = session.execute(text("""
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT p.id
            FROM processed_lots AS p
            JOIN lot_geo_snapshots AS g ON g.lot_id = p.id
            WHERE p.is_archived = false
              AND g.centroid_lat BETWEEN 57 AND 58
              AND g.centroid_lon BETWEEN 39 AND 40
            ORDER BY p.last_update DESC
            LIMIT 25
        """)).scalar_one()
        assert plan[0]["Plan"]["Actual Total Time"] >= 0

    @contextmanager
    def postgres_scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(api, "session_scope", postgres_scope)
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(type(api.settings), "production_configuration_errors", lambda _self: [])
    ready = api.readiness_check()
    assert ready["status"] == "ready"
    assert ready["checks"]["schema"] == SCHEMA_REVISION


def test_postgres_pool_reconnect_and_exhaustion_are_bounded(engine) -> None:
    reconnect_engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    admin_engine = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with reconnect_engine.connect() as connection:
            backend_pid = connection.scalar(text("SELECT pg_backend_pid()"))
        with admin_engine.connect() as connection:
            assert connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid}) is True
        with reconnect_engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        reconnect_engine.dispose()
        admin_engine.dispose()

    exhausted_engine = create_engine(
        DATABASE_URL,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    try:
        with exhausted_engine.connect():
            with pytest.raises(SQLAlchemyTimeoutError):
                exhausted_engine.connect()
    finally:
        exhausted_engine.dispose()


def test_postgres_canonical_source_consistency_duplicates_and_orphans(engine) -> None:
    with Session(engine) as session:
        processed = _lot("pg-consistency", source="ci", source_system="ci")
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="ci:pg-consistency",
            legacy_processed_lot_id=processed.id,
            title=processed.title,
            category=processed.category,
        )
        session.add(canonical)
        session.flush()
        source = SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=processed.id,
            source_system=processed.source_system,
            external_id=processed.external_id,
        )
        session.add(source)
        session.commit()

        assert session.scalar(
            select(SourceLot)
            .join(CanonicalLot, CanonicalLot.id == SourceLot.canonical_lot_id)
            .join(ProcessedLot, ProcessedLot.id == SourceLot.processed_lot_id)
            .where(SourceLot.id == source.id)
        ) is not None
        duplicate_groups = session.execute(text("""
            SELECT source_system, external_id
            FROM processed_lots
            GROUP BY source_system, external_id
            HAVING count(*) > 1
        """)).all()
        orphan_sources = session.scalar(text("""
            SELECT count(*)
            FROM source_lots AS s
            LEFT JOIN canonical_lots AS c ON c.id = s.canonical_lot_id
            WHERE c.id IS NULL
        """))
        orphan_geo = session.scalar(text("""
            SELECT count(*)
            FROM lot_geo_snapshots AS g
            LEFT JOIN processed_lots AS p ON p.id = g.lot_id
            WHERE p.id IS NULL
        """))
        assert duplicate_groups == []
        assert orphan_sources == 0
        assert orphan_geo == 0
