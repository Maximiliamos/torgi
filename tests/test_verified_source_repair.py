from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotSyncRun, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.verified_source_repair import repair_verified_source_lots


@pytest.fixture
def context():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    lot = NormalizedLot(external_id="test:1", source="test", source_system="test.ru",
                        title="Земельный участок", description="Земля", category="land",
                        region_slug="76", region_name="Ярославская область", address="Старый адрес",
                        cadastral_number="76:23:0000001:123", vin=None, area=100,
                        start_price=100, current_price=90, auction_status="active",
                        lot_url="https://test.ru/1", source_url="https://test.ru/1",
                        detail_level="detail", raw_data={}, application_deadline=datetime(2030, 1, 1))
    record = persist_lot(session, lot, isolate_source=True)
    record.review_status = "approved"
    source = session.scalar(select(SourceLot))
    source.organizer_contact = "Preserve existing contact"
    source.current_price = 80
    session.commit()
    values = asdict(lot)
    values.update(address="Проверенный новый адрес", start_price=1000,
                  current_price=None, application_deadline=None)
    observed = ["title", "description", "category", "address", "start_price", "auction_status", "detail_level"]
    values["raw_data"] = {"repair_observed_fields": observed, "repair_evidence_sha256": "a" * 64}
    proof = {"identity_verified": True, "external_id": "test:1", "sha256": "a" * 64,
             "checked_at_utc": datetime.now(timezone.utc).isoformat(),
             "source_url": "https://test.ru/1", "observed_fields": observed}
    evidence = [{"lot_id": record.id, "normalized": values, "verification": proof}]
    yield session, record, source, evidence
    session.close()
    engine.dispose()


def test_fresh_nonreported_source_repair_preserves_review_and_unobserved_fields(context):
    session, record, source, evidence = context
    scope = {record.id}
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids=scope)
    assert record.address == "Старый адрес" and not session.dirty
    states = {record.id: preview["items"][0]["expected_state"]}
    repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids=scope,
                                apply=True, expected_states=states,
                                expected_evidence_sha256=preview["evidence_sha256"])
    session.flush()
    assert record.address == source.address == "Проверенный новый адрес"
    assert float(record.start_price) == 1000
    assert float(record.current_price) == 90
    assert float(source.current_price) == 80
    assert source.application_deadline == datetime(2030, 1, 1)
    assert source.organizer_contact == "Preserve existing contact"
    assert source.raw_data["repair_evidence_sha256"] == "a" * 64
    assert record.review_status == "approved"


@pytest.mark.parametrize("invalid", ["identity", "stale", "host", "missing_field", "category", "hash"])
def test_unverified_or_inconsistent_evidence_cannot_write(context, invalid):
    session, record, source, evidence = context
    item = evidence[0]
    if invalid == "identity":
        item["verification"]["identity_verified"] = False
    elif invalid == "stale":
        item["verification"]["checked_at_utc"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    elif invalid == "host":
        item["verification"]["source_url"] = "https://wrong.example/1"
    elif invalid == "missing_field":
        item["normalized"]["start_price"] = None
    elif invalid == "category":
        item["normalized"]["category"] = "car"
    else:
        item["normalized"]["raw_data"]["repair_evidence_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True)
    assert record.address == source.address == "Старый адрес"


def test_exact_scope_and_preview_are_required(context):
    session, record, _, evidence = context
    with pytest.raises(ValueError, match="allowlist"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids=set())
    with pytest.raises(ValueError, match="preview"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True)


@pytest.mark.parametrize("source,url,accepted", [
    ("torgi-russia.ru", "https://xn----etbpba5admdlad.xn--p1ai/lot/1", True),
    ("torgi-russia.ru", "https://torgi-russia.ru/lot/1", True),
    ("torgi-russia.ru", "https://xn----etbpba5admdlad.xn--p1ai.evil.example/lot/1", False),
    ("torgi-russia.ru", "https://evilxn----etbpba5admdlad.xn--p1ai/lot/1", False),
    ("torgi-russia.ru", "https://xn----etbpba5admdlad.xn--p1ai@evil.example/lot/1", False),
    ("torgi-russia.ru", "http://xn----etbpba5admdlad.xn--p1ai/lot/1", False),
    ("test.ru", "https://xn----etbpba5admdlad.xn--p1ai/lot/1", False),
])
def test_source_proof_accepts_only_its_pinned_public_domain(context, source, url, accepted):
    session, record, source_row, evidence = context
    record.source_system = source_row.source_system = source
    evidence[0]["normalized"].update(source_system=source, source_url=url, lot_url=url)
    evidence[0]["verification"]["source_url"] = url
    session.commit()
    if not accepted:
        with pytest.raises(ValueError, match="exact source domain"):
            repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
        assert not session.dirty
        return
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                expected_states={record.id: preview["items"][0]["expected_state"]},
                                expected_evidence_sha256=preview["evidence_sha256"])
    session.flush()
    assert record.address == source_row.address == "Проверенный новый адрес"
    assert record.review_status == "approved"


@pytest.mark.parametrize("observation_kind", ["explicit", "legacy_run"])
def test_older_gis_proof_cannot_overwrite_a_newer_primary_ingestion(context, observation_kind):
    session, record, source, evidence = context
    observed_at = datetime.now(timezone.utc)
    record.source_system = source.source_system = "torgi.gov.ru"
    evidence[0]["normalized"]["source_system"] = "torgi.gov.ru"
    evidence[0]["verification"].update(
        source_url="https://torgi.gov.ru/1",
        checked_at_utc=(observed_at - timedelta(minutes=1)).isoformat(),
    )
    if observation_kind == "explicit":
        source.raw_data = {"ingestion_observed_at": observed_at.isoformat()}
    else:
        session.add(LotSyncRun(id="newer-primary-run", status="success", trigger_type="manual",
                               started_at=observed_at.replace(tzinfo=None)))
        source.last_sync_run_id = "newer-primary-run"
    session.commit()
    with pytest.raises(ValueError, match="older than the latest primary ingestion"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    assert record.address == source.address == "Старый адрес"


def test_collateral_claim_does_not_poison_shared_physical_asset(context):
    session, record, source, evidence = context
    original = session.get(CanonicalLot, source.canonical_lot_id)
    session.add(SourceLot(canonical_lot_id=original.id, source_system="other.ru", external_id="other:1"))
    session.commit()
    evidence[0]["normalized"].update(title="Право требования к должнику", category="receivable")
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                expected_states={record.id: preview["items"][0]["expected_state"]},
                                expected_evidence_sha256=preview["evidence_sha256"])
    session.flush()
    assert source.canonical_lot_id != original.id
    assert original.title == "Земельный участок" and original.category == "land"
    assert record.category == "receivable" and record.is_archived
    assert record.review_status == "approved"


def test_missing_source_reuses_existing_legacy_canonical(context):
    session, record, source, evidence = context
    canonical_id = source.canonical_lot_id
    session.delete(source)
    session.commit()
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    assert preview["items"][0]["restore_source_provenance"]
    repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                expected_states={record.id: preview["items"][0]["expected_state"]},
                                expected_evidence_sha256=preview["evidence_sha256"])
    session.flush()
    restored = session.scalar(select(SourceLot))
    assert restored.canonical_lot_id == canonical_id and restored.processed_lot_id == record.id
    assert session.get(ProcessedLot, record.id).review_status == "approved"


def test_canonical_edit_and_changed_desired_values_invalidate_preview(context):
    session, record, source, evidence = context
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    states = {record.id: preview["items"][0]["expected_state"]}
    evidence[0]["normalized"]["start_price"] = 2000
    with pytest.raises(ValueError, match="preview"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                    expected_states=states, expected_evidence_sha256=preview["evidence_sha256"])
    evidence[0]["normalized"]["start_price"] = 1000
    session.get(CanonicalLot, source.canonical_lot_id).address = "Concurrent canonical correction"
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                    expected_states=states, expected_evidence_sha256=preview["evidence_sha256"])


def test_older_snapshot_cannot_overwrite_a_newer_verified_field(context):
    session, record, source, evidence = context
    newer = datetime.now(timezone.utc).isoformat()
    source.raw_data = {"verified_source_fields": {"address": {"checked_at_utc": newer, "sha256": "b" * 64}}}
    session.commit()
    evidence[0]["verification"]["checked_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="older than"):
        repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    assert record.address == "Старый адрес"


def test_isolation_releases_other_legacy_owner_without_deleting_its_group(context):
    session, record, source, evidence = context
    shared = session.get(CanonicalLot, source.canonical_lot_id)
    shared.legacy_processed_lot_id = None
    orphan = CanonicalLot(canonical_key="old-owner", legacy_processed_lot_id=record.id,
                          title="Historical group", category="land")
    session.add_all([orphan, SourceLot(canonical_lot_id=shared.id, source_system="other.ru", external_id="other:2")])
    session.commit()
    values = evidence[0]["normalized"]
    values["cadastral_number"] = "76:23:0000001:999"
    values["raw_data"]["repair_observed_fields"].append("cadastral_number")
    evidence[0]["verification"]["observed_fields"].append("cadastral_number")
    preview = repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id})
    repair_verified_source_lots(session, evidence=evidence, allowed_lot_ids={record.id}, apply=True,
                                expected_states={record.id: preview["items"][0]["expected_state"]},
                                expected_evidence_sha256=preview["evidence_sha256"])
    session.flush()
    assert orphan.legacy_processed_lot_id is None
    assert orphan.title == "Historical group" and session.get(CanonicalLot, orphan.id) is orphan
    assert source.canonical_lot_id not in {orphan.id, shared.id}
    assert record.review_status == "approved"
