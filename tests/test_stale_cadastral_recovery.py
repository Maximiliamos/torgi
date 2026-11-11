from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import CadastralObjectResult
from bankrotai.services.stale_cadastral_recovery import (
    recover_stale_cadastral_live_results,
    recover_stale_cadastral_no_match,
)


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



def test_live_result_recovery_persists_exact_validated_result_without_second_probe() -> None:
    session = _session()
    lot_id = _seed(session)
    calls = []

    def probe(cadastral: str):
        calls.append(cadastral)
        return CadastralObjectResult(
            query=cadastral,
            cadastral_number=cadastral,
            address="Ярославская область, Ростовский район",
            lat=57.1,
            lon=39.4,
            source="nspd",
            confidence="high",
        ), "exact_match"

    result = recover_stale_cadastral_live_results(
        session,
        lot_ids=[lot_id],
        live_result_probe=probe,
        apply=True,
    )
    session.commit()

    lot = session.get(ProcessedLot, lot_id)
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    snapshots = session.query(LotGeoSnapshot).filter_by(lot_id=lot_id).all()

    assert calls == ["76:13:031301:92"]
    assert result["accepted"] == 1
    assert result["updated"] == 1
    assert lot.current_geo_lat == 57.1
    assert lot.current_geo_lon == 39.4
    assert lot.current_geo_source == "nspd"
    assert lot.current_geo_confidence == "high"
    assert lot.needs_geo_check is False
    assert lot.geo_input_hash is not None
    assert failure.status == "resolved"
    assert len(snapshots) == 1
    session.close()


def test_live_result_recovery_rejects_identity_mismatch_before_write() -> None:
    session = _session()
    lot_id = _seed(session)

    result = recover_stale_cadastral_live_results(
        session,
        lot_ids=[lot_id],
        live_result_probe=lambda cadastral: (
            CadastralObjectResult(
                query=cadastral,
                cadastral_number="76:13:031301:999",
                address="Ярославская область",
                lat=57.1,
                lon=39.4,
                source="nspd",
                confidence="high",
            ),
            "exact_match",
        ),
        apply=True,
    )

    lot = session.get(ProcessedLot, lot_id)
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    assert result["accepted"] == 0
    assert result["updated"] == 0
    assert result["rejections"] == [
        {"lot_id": lot_id, "reason": "live_nspd_identity_mismatch"}
    ]
    assert lot.current_geo_lat is None
    assert failure.status == "deferred_no_match"
    session.close()


def test_live_result_recovery_runs_normal_validation_before_write() -> None:
    session = _session()
    lot_id = _seed(session)
    lot = session.get(ProcessedLot, lot_id)
    lot.region_name = "Московская область"
    session.commit()

    result = recover_stale_cadastral_live_results(
        session,
        lot_ids=[lot_id],
        live_result_probe=lambda cadastral: (
            CadastralObjectResult(
                query=cadastral,
                cadastral_number=cadastral,
                address="Ярославская область, Ростовский район",
                lat=57.1,
                lon=39.4,
                source="nspd",
                confidence="high",
            ),
            "exact_match",
        ),
        apply=True,
    )

    assert result["accepted"] == 0
    assert result["updated"] == 0
    assert result["rejections"] == [
        {"lot_id": lot_id, "reason": "validation:region_cadastral_mismatch"}
    ]
    session.close()
