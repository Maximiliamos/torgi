from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, GeoFailure, ProcessedLot
from bankrotai.services.stale_cadastral_recovery import recover_stale_cadastral_no_match


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session, *, cadastral: str | None = "76:13:031301:92") -> int:
    lot = ProcessedLot(
        external_id=f"sample-{cadastral or 'none'}",
        source="test",
        source_system="torgi-russia.ru",
        title="Земельный участок",
        description="",
        category="land",
        cadastral_number=cadastral,
        auction_status="active",
    )
    session.add(lot)
    session.flush()
    session.add(GeoFailure(
        lot_id=lot.id,
        status="deferred_no_match",
        attempt_count=5,
        error_message="historical no match",
    ))
    session.commit()
    return int(lot.id)


def test_stale_cadastral_recovery_dry_run_requires_live_exact_proof() -> None:
    session = _session()
    lot_id = _seed(session)
    calls = []

    result = recover_stale_cadastral_no_match(
        session,
        lot_ids=[lot_id],
        live_probe=lambda cadastral: (calls.append(cadastral) or True, "exact_match"),
        apply=False,
    )

    assert calls == ["76:13:031301:92"]
    assert result["accepted"] == 1
    assert result["updated"] == 0
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    assert failure.status == "deferred_no_match"
    session.close()


def test_stale_cadastral_recovery_requeues_only_live_proven_exact_id() -> None:
    session = _session()
    accepted_id = _seed(session, cadastral="76:13:031301:92")
    rejected_id = _seed(session, cadastral="77:01:0004010:123")

    def probe(cadastral: str):
        return (
            (True, "exact_match")
            if cadastral == "76:13:031301:92"
            else (False, "live_exact_match_missing")
        )

    result = recover_stale_cadastral_no_match(
        session,
        lot_ids=[accepted_id, rejected_id],
        live_probe=probe,
        apply=True,
    )
    session.commit()

    accepted_failure = session.query(GeoFailure).filter_by(lot_id=accepted_id).one()
    rejected_failure = session.query(GeoFailure).filter_by(lot_id=rejected_id).one()
    accepted_lot = session.get(ProcessedLot, accepted_id)
    assert accepted_failure.status == "queued"
    assert accepted_failure.attempt_count == 0
    assert accepted_failure.next_retry_at is not None
    assert accepted_lot.needs_geo_check is True
    assert accepted_lot.geo_input_hash is None
    assert rejected_failure.status == "deferred_no_match"
    assert result["accepted"] == 1
    assert result["updated"] == 1
    session.close()


def test_stale_cadastral_recovery_rejects_missing_or_invalid_cadastre_without_probe() -> None:
    session = _session()
    lot_id = _seed(session, cadastral=None)
    calls = []

    result = recover_stale_cadastral_no_match(
        session,
        lot_ids=[lot_id],
        live_probe=lambda cadastral: (calls.append(cadastral) or True, "exact_match"),
        apply=True,
    )

    assert calls == []
    assert result["accepted"] == 0
    assert result["rejections"] == [{"lot_id": lot_id, "reason": "no_valid_cadastral"}]
    session.close()
