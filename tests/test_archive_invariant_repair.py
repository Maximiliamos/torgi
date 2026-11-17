import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot

spec = importlib.util.spec_from_file_location(
    "archive_repair", Path(__file__).resolve().parents[1] / "scripts/repair_closed_projection_invariants.py"
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class TestSession(Session):
        def execute(self, statement, *args, **kwargs):
            if str(statement).startswith("SET "):
                return None  # PostgreSQL transaction controls; real SQLite DML below.
            return super().execute(statement, *args, **kwargs)

    factory = lambda: TestSession(engine)
    monkeypatch.setattr(repair, "SessionLocal", factory)
    with factory() as session:
        canonical = CanonicalLot(canonical_key="test", title="Land")
        session.add(canonical)
        session.flush()
        for index, status in enumerate(["closed", "active"], 1):
            lot = ProcessedLot(external_id=str(index), source_system="test", source="test",
                               title="Land", category="land", auction_status="closed",
                               is_archived=False, review_status="approved")
            session.add(lot)
            session.flush()
            session.add(SourceLot(canonical_lot_id=canonical.id, processed_lot_id=lot.id,
                                  source_system="test", external_id=str(index),
                                  source_status=status, is_archived=status == "closed"))
        session.commit()
    return factory


def test_only_confirmed_closed_pair_is_repaired_and_rating_preserved(database):
    preview = repair.execute()
    assert preview["selected"] == 1
    trial = repair.execute(apply=True, expected_sha=preview["preview_sha256"], rollback_only=True)
    assert trial["updated"] == 1 and not trial["committed"]
    with database() as session:
        assert not session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "1")).is_archived
    result = repair.execute(apply=True, expected_sha=preview["preview_sha256"])
    assert result["committed"] and result["deleted_lots"] == 0
    with database() as session:
        rows = session.scalars(select(ProcessedLot).order_by(ProcessedLot.external_id)).all()
        assert rows[0].is_archived and rows[0].closed_at and rows[0].archived_at
        assert not rows[1].is_archived
        assert all(row.review_status == "approved" for row in rows)
    assert repair.execute()["selected"] == 0


def test_state_change_invalidates_preview(database):
    preview = repair.execute()
    with database() as session:
        session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "1")).review_status = "maybe"
        session.commit()
    with pytest.raises(RuntimeError, match="cohort/state changed"):
        repair.execute(apply=True, expected_sha=preview["preview_sha256"])
