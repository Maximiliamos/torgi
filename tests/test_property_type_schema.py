from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from bankrotai import core
from bankrotai.db import ProcessedLot, SCHEMA_REVISION


def test_classifier_widening_preserves_review_and_refuses_lossy_downgrade(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + str(tmp_path / "classifier.sqlite")
    monkeypatch.setattr(core, "_settings_cache", core.AppSettings(database_url=url, database_migration_url=url))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "4ef5a6b7c8d9")
    engine = create_engine(url)
    with Session(engine) as session:
        lot = ProcessedLot(external_id="official-type", source="arbbitlot", source_system="arbbitlot.ru",
                           title="Здание", description="Здание", category="real_estate",
                           auction_status="active", property_type="Склад", review_status="approved", rating=7.5)
        session.add(lot)
        session.commit()
        lot_id = lot.id
    command.upgrade(config, "head")
    column = next(x for x in inspect(engine).get_columns("processed_lots") if x["name"] == "property_type")
    assert str(column["type"]) == "TEXT"
    official = "Здания (помещения) жилые, не входящие в жилищный фонд"
    with Session(engine) as session:
        lot = session.get(ProcessedLot, lot_id)
        assert (lot.property_type, lot.review_status, lot.rating) == ("Склад", "approved", 7.5)
        lot.property_type = official
        session.commit()
    with pytest.raises(RuntimeError, match="without losing"):
        command.downgrade(config, "4ef5a6b7c8d9")
    with Session(engine) as session:
        assert session.get(ProcessedLot, lot_id).property_type == official
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
    engine.dispose()
