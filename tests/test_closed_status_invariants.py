from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotStatusHistory, ProcessedLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import apply_lot_status, persist_lot


def test_repeated_closed_evidence_repairs_flags_without_fake_transition():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(external_id="closed-legacy", source="test", source_system="test",
                           title="Land", description="", category="land", auction_status="closed",
                           is_archived=False, review_status="approved")
        session.add(lot)
        session.flush()
        assert apply_lot_status(session, lot, "closed", "verified-source") is False
        assert lot.is_archived and lot.archived_at and lot.closed_at
        assert lot.review_status == "approved"
        assert session.scalars(select(LotStatusHistory)).all() == []
        timestamps = (lot.archived_at, lot.closed_at)
        apply_lot_status(session, lot, "closed", "verified-source")
        assert (lot.archived_at, lot.closed_at) == timestamps


def test_first_import_of_closed_lot_archives_projection():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = persist_lot(session, NormalizedLot(
            external_id="closed-new", source="test", source_system="test",
            title="Land", description="", category="land", auction_status="closed",
            region_slug=None, region_name=None, address=None, cadastral_number=None,
            vin=None, area=None, start_price=None, current_price=None,
            lot_url=None, source_url=None, detail_level="detail", raw_data={},
        ), isolate_source=True)
        assert lot.is_archived and lot.archived_at and lot.closed_at
        assert len(session.scalars(select(LotStatusHistory)).all()) == 1
