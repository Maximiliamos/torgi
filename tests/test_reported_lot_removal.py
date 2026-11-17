import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotIssueReport, ProcessedLot, SourceLot, CanonicalLot, LotNote
from bankrotai.services.reported_lot_removal import remove_reviewed_reported_lots


def seed():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    session = Session(engine)
    lot = ProcessedLot(external_id="test:1", source="test", source_system="test.ru",
                       title="Ошибка", description="Ошибка", category="land", review_status="rejected")
    other = ProcessedLot(external_id="test:2", source="test", source_system="other.ru",
                         title="Другой источник", description="Сохранить", category="land", review_status="approved")
    session.add_all([lot, other]); session.flush()
    canonical = CanonicalLot(canonical_key="shared:1", legacy_processed_lot_id=lot.id, title="Участок", category="land")
    session.add(canonical); session.flush()
    session.add_all([SourceLot(source_system="test.ru", external_id="test:1", canonical_lot_id=canonical.id, processed_lot_id=lot.id),
                     SourceLot(source_system="other.ru", external_id="test:2", canonical_lot_id=canonical.id, processed_lot_id=other.id),
                     LotIssueReport(lot_id=lot.id, user_id="operator", content="Неверные данные")])
    session.commit()
    return session, lot.id, other.id, canonical.id


def test_removal_preserves_other_source_and_historical_report():
    session, selected, other, canonical = seed()
    preview = remove_reviewed_reported_lots(session, lot_ids=[selected])
    assert not session.dirty
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}
    result = remove_reviewed_reported_lots(session, lot_ids=[selected], apply=True, expected_states=states)
    session.expire_all()
    assert result["deleted_lots"] == 1
    assert session.get(ProcessedLot, selected) is None
    assert session.get(ProcessedLot, other).review_status == "approved"
    assert session.get(CanonicalLot, canonical) is not None
    assert session.scalar(select(func.count()).select_from(SourceLot)) == 1
    assert session.scalar(select(func.count()).select_from(LotIssueReport)) == 1


def test_unreviewed_unreported_or_concurrently_modified_lots_are_rejected():
    session, selected, other, _ = seed()
    with pytest.raises(ValueError, match="cohort"):
        remove_reviewed_reported_lots(session, lot_ids=[other])
    preview = remove_reviewed_reported_lots(session, lot_ids=[selected])
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}
    session.get(ProcessedLot, selected).title = "Concurrent edit"
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        remove_reviewed_reported_lots(session, lot_ids=[selected], apply=True, expected_states=states)
    session.get(ProcessedLot, selected).review_status = None
    session.flush()
    with pytest.raises(ValueError, match="reviewed"):
        remove_reviewed_reported_lots(session, lot_ids=[selected])
