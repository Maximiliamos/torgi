from dataclasses import asdict

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotIssueReport, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.reported_lot_repair import repair_reported_lots


def fixture(review_status=None):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    lot = NormalizedLot(external_id="test:1", source="test", source_system="test.ru",
                        title="Земельный участок", description="Продажа участка",
                        category="land", region_slug="76", region_name="Ярославская область",
                        address="Старый адрес", cadastral_number="76:23:0000001:123", vin=None,
                        area=None, start_price=100, current_price=10, auction_status="active",
                        lot_url="https://test.ru/1", source_url="https://test.ru/1",
                        detail_level="detail", raw_data={"cadastral_numbers": ["76:23:0000001:123"]})
    record = persist_lot(session, lot, isolate_source=True)
    record.review_status = review_status
    record.current_geo_lat, record.current_geo_lon = 57.1, 39.1
    session.add(LotIssueReport(lot_id=record.id, user_id="operator", content="Неверная цена и адрес"))
    session.commit()
    lot.start_price = 1000
    lot.current_price = None
    lot.address = "Новый адрес"
    evidence = [{"lot_id": record.id, "normalized": asdict(lot)}]
    return session, record, evidence


def test_preview_is_readonly_and_reviewed_rows_are_protected():
    session, record, evidence = fixture("approved")
    plan = repair_reported_lots(session, evidence=evidence)
    assert plan["protected"] == 1
    assert not session.dirty
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True)
    session.flush()
    assert record.address == "Старый адрес"
    assert record.review_status == "approved"
    assert float(record.start_price) == 100


def test_exact_repair_keeps_review_and_clears_stale_price_and_geo():
    session, record, evidence = fixture("maybe")
    plan = repair_reported_lots(session, evidence=evidence, allow_reviewed_source_fields=True)
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True, allow_reviewed_source_fields=True)
    session.flush()
    source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == record.id))
    canonical = session.get(CanonicalLot, source.canonical_lot_id)
    assert record.review_status == "maybe"
    assert record.current_price is source.current_price is None
    assert record.address == source.address == canonical.address == "Новый адрес"
    assert float(record.start_price) == float(source.start_price) == 1000
    assert record.current_geo_lat is None and record.needs_geo_check
    assert not repair_reported_lots(session, evidence=evidence)["items"][0]["changes"]


def test_changed_identity_or_stale_preview_never_writes():
    session, record, evidence = fixture()
    plan = repair_reported_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    record.address = "Concurrent edit"
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True)
    evidence[0]["normalized"]["external_id"] = "test:2"
    with pytest.raises(ValueError, match="identity mismatch"):
        repair_reported_lots(session, evidence=evidence)


def test_receivable_is_archived_without_deleting_report_or_review():
    session, record, evidence = fixture("rejected")
    evidence[0]["normalized"].update(category="receivables", title="Право требования по ДДУ на квартиру")
    plan = repair_reported_lots(session, evidence=evidence, allow_reviewed_source_fields=True)
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True, allow_reviewed_source_fields=True)
    session.flush()
    assert record.is_archived and not record.needs_geo_check
    assert record.review_status == "rejected"
    assert session.scalar(select(func.count()).select_from(LotIssueReport)) == 1


def test_poisoned_source_cadastre_does_not_overwrite_shared_canonical():
    session, record, evidence = fixture()
    source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == record.id))
    old = session.get(CanonicalLot, source.canonical_lot_id)
    session.add(SourceLot(canonical_lot_id=old.id, source_system="another.ru", external_id="other:1"))
    session.commit()
    evidence[0]["normalized"]["cadastral_number"] = "43:40:0000421:359"
    evidence[0]["normalized"]["raw_data"]["cadastral_numbers"] = ["43:40:0000421:359"]
    plan = repair_reported_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True)
    session.flush()
    assert source.canonical_lot_id != old.id
    assert old.cadastral_number == "76:23:0000001:123"
    assert session.get(CanonicalLot, source.canonical_lot_id).cadastral_number == record.cadastral_number


def test_orphaned_exact_source_is_relinked_without_duplicate_source_rows():
    session, record, evidence = fixture()
    source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == record.id))
    source.processed_lot_id = None
    session.commit()
    plan = repair_reported_lots(session, evidence=evidence)
    assert plan["items"][0]["restore_source_provenance"]
    states = {item["lot_id"]: item["expected_state"] for item in plan["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True)
    session.flush()
    assert source.processed_lot_id == record.id
    assert session.scalar(select(func.count()).select_from(SourceLot)) == 1


def test_preexisting_closed_status_repairs_inconsistent_archive_flags():
    session, record, evidence = fixture()
    record.auction_status = "closed"
    record.is_archived = False
    session.commit()
    evidence[0]["normalized"].update(auction_status="closed", region_slug=None, region_name=None)
    preview = repair_reported_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}
    repair_reported_lots(session, evidence=evidence, expected_states=states, apply=True)
    session.flush()
    source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == record.id))
    assert record.is_archived and source.is_archived and not source.is_active
    assert record.region_code == "76" and record.region_name == "Ярославская область"
