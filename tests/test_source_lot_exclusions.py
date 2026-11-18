from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, ProcessedLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.batch_persistence import persist_changed_lots_batch
from bankrotai.services.source_lot_exclusions import (
    SourceLotExcludedError, excluded_ids, register_exclusion,
)


def test_deletion_exclusion_survives_both_writers_and_is_source_scoped():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    lot = NormalizedLot(source="test", source_system="first.ru", external_id="same-id",
                        title="Земельный участок", description="", category="land",
                        region_slug=None, region_name=None, address=None, cadastral_number=None,
                        vin=None, area=None, start_price=100, current_price=100, auction_status="active",
                        lot_url="https://first.ru/lot", source_url="https://first.ru/lot",
                        detail_level="detail", raw_data={})
    with Session(engine) as session:
        assert register_exclusion(session, source_system=lot.source_system, external_id=lot.external_id, reason="user deletion")
        session.commit()
    with Session(engine) as session:
        assert not register_exclusion(session, source_system=lot.source_system, external_id=lot.external_id, reason="repeat")
        with pytest.raises(SourceLotExcludedError):
            persist_lot(session, lot)
        with pytest.raises(SourceLotExcludedError):
            persist_changed_lots_batch(session, [lot], "test-run")
        assert session.scalars(select(ProcessedLot)).all() == []
        other = replace(lot, source_system="second.ru", source_url="https://second.ru/lot", lot_url="https://second.ru/lot")
        persist_lot(session, other, isolate_source=True)
        session.commit()
        assert excluded_ids(session, "second.ru", ["same-id"]) == set()
        assert session.scalars(select(ProcessedLot)).one().source_system == "second.ru"
