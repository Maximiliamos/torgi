from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from bankrotai import core


pytestmark = pytest.mark.postgres
DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
PRE_MAP_REVISION = "e6f7a8b9c0d1"


def test_postgres_map_migration_fresh_upgrade_existing_data_rollback_and_duplicate_repair() -> None:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(DATABASE_URL)
    core._settings_cache = core.AppSettings(database_url=DATABASE_URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))

    def reset() -> None:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

    try:
        # A: fresh PostgreSQL install.
        reset()
        command.upgrade(config, "head")
        assert {"map_datasets", "map_tiles"} <= set(inspect(engine).get_table_names())

        # B: representative previous schema with application and geo data.
        reset()
        command.upgrade(config, PRE_MAP_REVISION)
        with engine.begin() as connection:
            lot_id = connection.scalar(text("""
                INSERT INTO processed_lots
                     (external_id, source, source_system, title, description, category,
                     auction_status, is_archived, needs_human_review, last_update)
                VALUES
                    ('migration-lot', 'test', 'test', 'Migration lot', '', 'land',
                     'active', false, false, NOW())
                RETURNING id
            """))
            connection.execute(text("""
                INSERT INTO lot_geo_snapshots
                    (lot_id, geo_source, geo_method, geo_confidence, centroid_lat,
                     centroid_lon, observed_at)
                VALUES (:lot_id, 'test', 'fixture', 'high', 57.6, 39.8, NOW())
            """), {"lot_id": lot_id})
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM processed_lots WHERE id=:id"), {"id": lot_id}) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM lot_geo_snapshots WHERE lot_id=:id"), {"id": lot_id}) == 1

        # C: downgrade removes only the map feature and preserves application data.
        command.downgrade(config, PRE_MAP_REVISION)
        assert "map_datasets" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM processed_lots WHERE id=:id"), {"id": lot_id}) == 1

        # D: repair a pre-release create_all schema containing duplicate current rows.
        reset()
        command.upgrade(config, PRE_MAP_REVISION)
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE map_datasets (
                    id SERIAL PRIMARY KEY, version VARCHAR(64) NOT NULL UNIQUE,
                    status VARCHAR(20) NOT NULL, is_current BOOLEAN NOT NULL,
                    point_count INTEGER NOT NULL, tile_count INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL, published_at TIMESTAMP NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE map_tiles (
                    id SERIAL PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES map_datasets(id) ON DELETE CASCADE,
                    z INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
                    feature_count INTEGER NOT NULL, etag VARCHAR(64) NOT NULL, payload_json JSON NOT NULL,
                    CONSTRAINT uq_map_tile_coordinate UNIQUE(dataset_id, z, x, y)
                )
            """))
            connection.execute(text("""
                INSERT INTO map_datasets
                    (version, status, is_current, point_count, tile_count, created_at, published_at)
                VALUES
                    ('older-current', 'ready', true, 1, 0, '2026-01-01', '2026-01-01'),
                    ('newer-current', 'ready', true, 1, 0, '2026-02-01', '2026-02-01')
            """))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalars(text("SELECT version FROM map_datasets WHERE is_current")).all() == ["newer-current"]
            index_definition = connection.scalar(text("""
                SELECT indexdef FROM pg_indexes
                WHERE schemaname='public' AND indexname='uq_map_datasets_single_current'
            """))
            assert index_definition is not None and "UNIQUE INDEX" in index_definition
        command.downgrade(config, PRE_MAP_REVISION)
        assert "map_datasets" not in inspect(engine).get_table_names()
    finally:
        # Leave the isolated test database at head for the rest of the suite.
        command.upgrade(config, "head")
        engine.dispose()
        core._settings_cache = None
