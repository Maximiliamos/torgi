import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotNote, ProcessedLot
from bankrotai.logic import reconcile_cross_source_duplicates
from bankrotai.services.legacy_duplicate_repair import repair_duplicate_links


@pytest.fixture
def context():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        parent = ProcessedLot(source_system="one.ru", external_id="one:1", title="Участок", category="land",
                              cadastral_number="76:23:0000001:1", lot_url="https://one.ru/auction/lot/1",
                              auction_status="active", review_status="rejected")
        session.add(parent)
        session.flush()
        child = ProcessedLot(source_system="two.ru", external_id="two:2", title="Другие торги",
                             category="land", cadastral_number=parent.cadastral_number,
                             lot_url="https://two.ru/auction/lot/2", duplicate_of_id=parent.id,
                             auction_status="active", review_status="approved", rating=71,
                             needs_geo_check=False)
        session.add(child)
        session.flush()
        note = LotNote(lot_id=child.id, user_id="operator", content="Keep my note on this publication")
        session.add(note)
        session.commit()
        yield session, parent, child, note
    engine.dispose()


@pytest.mark.parametrize("same_sale", [False, True])
def test_repair_requires_sale_identity_and_preserves_reviews_and_note_ids(context, same_sale):
    session, parent, child, note = context
    if same_sale:
        child.lot_url = parent.lot_url
        session.commit()
    preview = repair_duplicate_links(session, lot_ids=[child.id])
    assert preview["repairable"] == (0 if same_sale else 1)
    assert child.duplicate_of_id == parent.id and not session.dirty
    repair_duplicate_links(session, lot_ids=[child.id], apply=True,
                            expected_states={child.id: preview["items"][0]["expected_state"]},
                            expected_selection_sha256=preview["selection_sha256"])
    assert child.duplicate_of_id == (parent.id if same_sale else None)
    assert child.needs_geo_check == (not same_sale)
    assert child.review_status == "approved" and child.rating == 71
    assert parent.review_status == "rejected"
    assert note.lot_id == child.id and note.content == "Keep my note on this publication"
    assert len(list(session.scalars(select(ProcessedLot)))) == 2


def test_changed_parent_invalidates_duplicate_repair_preview(context):
    session, parent, child, _ = context
    preview = repair_duplicate_links(session, lot_ids=[child.id])
    parent.cadastral_number = "77:01:0000001:1"
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        repair_duplicate_links(session, lot_ids=[child.id], apply=True,
                                expected_states={child.id: preview["items"][0]["expected_state"]},
                                expected_selection_sha256=preview["selection_sha256"])
    assert child.duplicate_of_id == parent.id


def test_normal_reconciliation_cannot_keep_a_legacy_different_sale_link(context):
    session, parent, child, note = context
    assert reconcile_cross_source_duplicates(session) == 0
    assert session.info["duplicate_links_detached"] == 1
    assert child.duplicate_of_id is None
    assert child.review_status == "approved" and parent.review_status == "rejected"
    assert note.lot_id == child.id
    assert reconcile_cross_source_duplicates(session) == 0
    assert session.info["duplicate_links_detached"] == 0
