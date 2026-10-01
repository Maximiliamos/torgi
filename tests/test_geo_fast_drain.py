from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from bankrotai.core import utc_now
from bankrotai.db import Base, GeoFailure, ProcessedLot
from bankrotai.services.geo_backfill import geocoding_progress
from bankrotai.services.geo_fast_drain import (
    P7_HOLD_STATUS,
    geo_fast_drain_plan,
    reclassify_legacy_geo_backlog,
    release_geo_fast_drain_wave,
)


def _lot(external_id: str, *, region_code: str = "76") -> ProcessedLot:
    return ProcessedLot(
        external_id=external_id,
        source="test",
        source_system="test",
        title="Недвижимость",
        description="",
        category="real_estate",
        region_code=region_code,
        address="Ярославль, улица Свободы, 1",
        auction_status="active",
    )


def _legacy_no_match(attempts: int = 5) -> GeoFailure:
    return GeoFailure(
        lot_id=0,
        status="queued",
        attempt_count=attempts,
        error_message='{"error":"No validated geocoding result","attempts":[{"source":"address_geocoder","valid":false,"reason":"no_coordinates"}]}',
        last_failed_at=utc_now(),
        next_retry_at=utc_now() + timedelta(days=4),
    )


def test_p7_reclassifies_old_no_match_into_held_queue_with_one_fresh_attempt_left() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = _lot("p7-no-match")
        session.add(lot)
        session.flush()
        failure = _legacy_no_match()
        failure.lot_id = lot.id
        session.add(failure)
        session.commit()

        result = reclassify_legacy_geo_backlog(session, apply=True)
        session.refresh(failure)

        assert result["changed"] == 1
        assert result["legacy_by_classification"] == {"no_match": 1}
        assert failure.status == P7_HOLD_STATUS
        assert failure.attempt_count == 2
        assert failure.next_retry_at is not None
        assert failure.next_retry_at > utc_now()
        assert '"p7_reclassified":true' in failure.error_message
        assert '"legacy_attempt_count":5' in failure.error_message


def test_p7_operational_history_does_not_consume_semantic_attempt_budget() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = _lot("p7-network")
        session.add(lot)
        session.flush()
        failure = GeoFailure(
            lot_id=lot.id,
            status="queued",
            attempt_count=7,
            error_message='{"error":"Read timed out","attempts":[{"source":"nspd","valid":false,"reason":"read_timeout","operational":true}]}',
            last_failed_at=utc_now(),
            next_retry_at=utc_now() + timedelta(days=7),
        )
        session.add(failure)
        session.commit()

        reclassify_legacy_geo_backlog(session, apply=True)
        session.refresh(failure)

        assert failure.status == "network_wait"
        assert failure.attempt_count == 0
        assert failure.next_retry_at is not None
        assert failure.next_retry_at <= utc_now() + timedelta(minutes=2)


def test_p7_release_wave_prioritizes_cfo() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        outside = _lot("outside", region_code="23")
        cfo = _lot("cfo", region_code="76")
        session.add_all([outside, cfo])
        session.flush()
        for lot in (outside, cfo):
            failure = _legacy_no_match()
            failure.lot_id = lot.id
            session.add(failure)
        session.commit()
        reclassify_legacy_geo_backlog(session, apply=True)

        result = release_geo_fast_drain_wave(session, limit=1)

        assert result["released"] == 1
        assert result["released_cfo"] == 1
        cfo_failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == cfo.id))
        outside_failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == outside.id))
        assert cfo_failure is not None and cfo_failure.next_retry_at <= utc_now()
        assert outside_failure is not None and outside_failure.next_retry_at > utc_now()


def test_p7_held_work_is_not_reported_as_runnable_retry(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(
        "bankrotai.services.geo_backfill.network_health_snapshot",
        lambda: {"external": {"circuit_open": False}},
    )
    with SessionLocal() as session:
        lot = _lot("held")
        session.add(lot)
        session.flush()
        failure = _legacy_no_match()
        failure.lot_id = lot.id
        session.add(failure)
        session.commit()
        reclassify_legacy_geo_backlog(session, apply=True)

        progress = geocoding_progress(session)

        assert progress["p7_held"] == 1
        assert progress["eligible_now"] == 0
        assert progress["waiting_for_retry"] == 0
        assert progress["actionable_remaining"] == 0
        assert progress["drain_remaining"] == 1


def test_p7_plan_is_idempotent_after_reclassification() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = _lot("idempotent")
        session.add(lot)
        session.flush()
        failure = _legacy_no_match()
        failure.lot_id = lot.id
        session.add(failure)
        session.commit()

        assert geo_fast_drain_plan(session)["legacy_total"] == 1
        reclassify_legacy_geo_backlog(session, apply=True)
        assert geo_fast_drain_plan(session)["legacy_total"] == 0
        assert geo_fast_drain_plan(session)["p7_held"] == 1
