from dataclasses import asdict

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.verified_source_repair import (
    evidence_sha256,
    repair_verified_source_lots,
)


def _fixture(review_status=None):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    lot = NormalizedLot(
        external_id="verified:1",
        source="test",
        source_system="test.ru",
        title="Земельный участок",
        description="Продажа участка",
        category="land",
        region_slug="76",
        region_name="Ярославская область",
        address="Старый адрес",
        cadastral_number="76:23:0000001:123",
        vin=None,
        area=None,
        start_price=100,
        current_price=90,
        auction_status="active",
        lot_url="https://test.ru/1",
        source_url="https://test.ru/1",
        detail_level="detail",
        raw_data={"cadastral_numbers": ["76:23:0000001:123"]},
    )
    record = persist_lot(session, lot, isolate_source=True)
    record.review_status = review_status
    record.current_geo_lat = 57.1
    record.current_geo_lon = 39.1
    session.commit()

    lot.address = "Новый адрес"
    lot.start_price = 1000
    lot.current_price = None
    lot.raw_data["repair_observed_fields"] = ["address", "start_price"]
    normalized = asdict(lot)
    evidence = [{
        "lot_id": record.id,
        "source_system": lot.source_system,
        "external_id": lot.external_id,
        "normalized": normalized,
        "sha256": evidence_sha256(normalized),
    }]
    return session, record, evidence


def test_verified_repair_requires_exact_evidence_hash() -> None:
    session, _record, evidence = _fixture()
    evidence[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        repair_verified_source_lots(session, evidence=evidence)


def test_verified_repair_requires_explicit_observed_fields() -> None:
    session, _record, evidence = _fixture()
    evidence[0]["normalized"]["raw_data"].pop("repair_observed_fields")
    evidence[0]["sha256"] = evidence_sha256(evidence[0]["normalized"])
    with pytest.raises(ValueError, match="repair_observed_fields"):
        repair_verified_source_lots(session, evidence=evidence)


def test_verified_repair_updates_only_observed_fields_and_requeues_geo() -> None:
    session, record, evidence = _fixture()
    preview = repair_verified_source_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}

    repair_verified_source_lots(
        session,
        evidence=evidence,
        apply=True,
        expected_states=states,
    )
    session.flush()

    source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == record.id))
    assert record.address == source.address == "Новый адрес"
    assert float(record.start_price) == float(source.start_price) == 1000
    # current_price was hidden/unobserved by the evidence and must be retained.
    assert float(record.current_price) == float(source.current_price) == 90
    assert record.current_geo_lat is None
    assert record.current_geo_lon is None
    assert record.needs_geo_check is True


def test_reviewed_rows_remain_protected_without_explicit_override() -> None:
    session, record, evidence = _fixture("rejected")
    preview = repair_verified_source_lots(session, evidence=evidence)
    assert preview["protected"] == 1
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}

    repair_verified_source_lots(
        session,
        evidence=evidence,
        apply=True,
        expected_states=states,
    )
    session.flush()

    assert record.address == "Старый адрес"
    assert record.review_status == "rejected"


def test_verified_repair_rejects_stale_preview_and_identity_mismatch() -> None:
    session, record, evidence = _fixture()
    preview = repair_verified_source_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}
    record.title = "Concurrent edit"
    session.flush()

    with pytest.raises(ValueError, match="stale"):
        repair_verified_source_lots(
            session,
            evidence=evidence,
            apply=True,
            expected_states=states,
        )

    evidence[0]["normalized"]["external_id"] = "verified:2"
    evidence[0]["sha256"] = evidence_sha256(evidence[0]["normalized"])
    with pytest.raises(ValueError, match="not present"):
        repair_verified_source_lots(session, evidence=evidence)


def test_verified_nonproperty_source_truth_archives_without_delete() -> None:
    session, record, evidence = _fixture()
    evidence[0]["normalized"].update(
        title="Грузовой автомобиль МАЗ",
        category="car",
    )
    evidence[0]["normalized"]["raw_data"]["repair_observed_fields"] = [
        "title", "category"
    ]
    evidence[0]["sha256"] = evidence_sha256(evidence[0]["normalized"])

    preview = repair_verified_source_lots(session, evidence=evidence)
    states = {item["lot_id"]: item["expected_state"] for item in preview["items"]}
    result = repair_verified_source_lots(
        session,
        evidence=evidence,
        apply=True,
        expected_states=states,
    )
    session.flush()

    assert result["deleted_lots"] == 0
    assert record.is_archived is True
    assert record.category == "car"
    assert session.get(ProcessedLot, record.id) is record
