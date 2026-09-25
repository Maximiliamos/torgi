from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.core import utc_now
from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun
from bankrotai.services.quality import list_source_health


def _sessions():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_source_freshness_keeps_fast_recency_and_complete_coverage_separate() -> None:
    factory = _sessions()
    now = utc_now()
    with factory() as session:
        complete_run = LotSyncRun(
            id="complete",
            triggered_by="test",
            trigger_type="scheduled_full",
            status="success",
            total_sources=1,
            started_at=now - timedelta(hours=12, minutes=10),
            finished_at=now - timedelta(hours=12),
        )
        fast_run = LotSyncRun(
            id="fast",
            triggered_by="test",
            trigger_type="scheduled_fast",
            status="success",
            total_sources=1,
            started_at=now - timedelta(minutes=35),
            finished_at=now - timedelta(minutes=30),
        )
        session.add_all([complete_run, fast_run])
        session.flush()
        session.add_all([
            LotSyncSourceRun(
                sync_run_id="complete",
                source_system="torgi.gov.ru",
                status="success",
                complete_source_run=True,
                items_seen=500,
                duration_ms=600_000,
                started_at=complete_run.started_at,
                finished_at=complete_run.finished_at,
            ),
            LotSyncSourceRun(
                sync_run_id="fast",
                source_system="torgi.gov.ru",
                status="success",
                complete_source_run=False,
                items_seen=50,
                duration_ms=60_000,
                started_at=fast_run.started_at,
                finished_at=fast_run.finished_at,
            ),
        ])
        session.commit()

        [health] = list_source_health(session)

    assert health.source_system == "torgi.gov.ru"
    assert health.status == "partial"
    assert health.freshness_status == "fresh"
    assert health.coverage_status == "fresh"
    assert health.last_complete_source_run is False
    assert health.last_success_at == fast_run.finished_at
    assert health.last_complete_success_at == complete_run.finished_at
    assert 0 <= (health.freshness_age_seconds or 0) <= 3600
    assert 11 * 3600 <= (health.complete_snapshot_age_seconds or 0) <= 13 * 3600


def test_latest_failure_is_visible_without_losing_last_success() -> None:
    factory = _sessions()
    now = utc_now()
    with factory() as session:
        success = LotSyncRun(
            id="success",
            triggered_by="test",
            trigger_type="scheduled_full",
            status="success",
            total_sources=1,
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=50),
        )
        failure = LotSyncRun(
            id="failure",
            triggered_by="test",
            trigger_type="scheduled_fast",
            status="failed",
            total_sources=1,
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=10),
        )
        session.add_all([success, failure])
        session.flush()
        session.add_all([
            LotSyncSourceRun(
                sync_run_id="success",
                source_system="tbankrot.ru",
                status="success",
                complete_source_run=True,
                items_seen=400,
                started_at=success.started_at,
                finished_at=success.finished_at,
            ),
            LotSyncSourceRun(
                sync_run_id="failure",
                source_system="tbankrot.ru",
                status="failed",
                complete_source_run=False,
                items_seen=0,
                error_message="temporary upstream error",
                started_at=failure.started_at,
                finished_at=failure.finished_at,
            ),
        ])
        session.commit()

        [health] = list_source_health(session)

    assert health.status == "failed"
    assert health.freshness_status == "failed"
    assert health.coverage_status == "fresh"
    assert health.last_success_at == success.finished_at
    assert health.last_failure_at == failure.finished_at
    assert health.last_error == "temporary upstream error"
