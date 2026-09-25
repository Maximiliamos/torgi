from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun, MapDataset
from bankrotai.services.production_health import build_phase3_health


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _healthy_source(session, now: datetime) -> None:
    run = LotSyncRun(
        id="full-healthy",
        triggered_by="test",
        trigger_type="scheduled_full",
        status="success",
        total_sources=1,
        started_at=(now - timedelta(hours=1)).replace(tzinfo=None),
        finished_at=(now - timedelta(minutes=50)).replace(tzinfo=None),
    )
    session.add(run)
    session.flush()
    session.add(
        LotSyncSourceRun(
            sync_run_id=run.id,
            source_system="torgi.gov.ru",
            status="success",
            complete_source_run=True,
            pages_scanned=10,
            items_seen=100,
            duration_ms=1000,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
    )


def test_phase3_health_accepts_current_map_and_fresh_complete_source() -> None:
    factory = _factory()
    now = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
    with factory() as session:
        session.add(
            MapDataset(
                version="healthy-r6-bundle-s3",
                status="ready",
                is_current=True,
                point_count=100,
                tile_count=200,
                created_at=(now - timedelta(hours=2)).replace(tzinfo=None),
                published_at=(now - timedelta(hours=1)).replace(tzinfo=None),
            )
        )
        _healthy_source(session, now)
        session.commit()

        health = build_phase3_health(session, now=now, expected_sources={"torgi.gov.ru"})

    assert health["healthy"] is True
    assert health["critical_failure_count"] == 0
    assert health["summary"]["map_version"] == "healthy-r6-bundle-s3"
    checks = {item["name"]: item for item in health["checks"]}
    assert checks["source-freshness:torgi.gov.ru"]["ok"] is True
    assert checks["source-coverage:torgi.gov.ru"]["ok"] is True
    assert checks["geo-backlog-liveness"]["ok"] is True


def test_phase3_health_fails_for_expired_sync_lease_and_failed_newer_map() -> None:
    factory = _factory()
    now = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
    with factory() as session:
        session.add(
            MapDataset(
                version="current-r6-bundle-s3",
                status="ready",
                is_current=True,
                point_count=100,
                tile_count=200,
                created_at=(now - timedelta(hours=3)).replace(tzinfo=None),
                published_at=(now - timedelta(hours=2)).replace(tzinfo=None),
            )
        )
        session.add(
            MapDataset(
                version="failed-next",
                status="failed",
                is_current=False,
                point_count=0,
                tile_count=0,
                created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
            )
        )
        session.add(
            LotSyncRun(
                id="stuck",
                triggered_by="scheduler",
                trigger_type="scheduled_fast",
                status="running",
                total_sources=1,
                started_at=(now - timedelta(hours=2)).replace(tzinfo=None),
                heartbeat_at=(now - timedelta(hours=2)).replace(tzinfo=None),
                lease_expires_at=(now - timedelta(hours=1)).replace(tzinfo=None),
            )
        )
        _healthy_source(session, now)
        session.commit()

        health = build_phase3_health(session, now=now, expected_sources={"torgi.gov.ru"})

    assert health["healthy"] is False
    checks = {item["name"]: item for item in health["checks"]}
    assert checks["map-publication-last-attempt"]["ok"] is False
    assert checks["source-sync-lease"]["ok"] is False


def test_phase3_health_reports_missing_source_coverage_as_critical() -> None:
    factory = _factory()
    now = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
    with factory() as session:
        session.add(
            MapDataset(
                version="healthy-r6-bundle-s3",
                status="ready",
                is_current=True,
                point_count=100,
                tile_count=200,
                created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                published_at=(now - timedelta(minutes=30)).replace(tzinfo=None),
            )
        )
        fast = LotSyncRun(
            id="fast-only",
            triggered_by="test",
            trigger_type="scheduled_fast",
            status="success",
            total_sources=1,
            started_at=(now - timedelta(minutes=30)).replace(tzinfo=None),
            finished_at=(now - timedelta(minutes=20)).replace(tzinfo=None),
        )
        session.add(fast)
        session.flush()
        session.add(
            LotSyncSourceRun(
                sync_run_id=fast.id,
                source_system="tbankrot.ru",
                status="success",
                complete_source_run=False,
                items_seen=10,
                started_at=fast.started_at,
                finished_at=fast.finished_at,
            )
        )
        session.commit()

        health = build_phase3_health(session, now=now, expected_sources={"tbankrot.ru"})

    assert health["healthy"] is False
    checks = {item["name"]: item for item in health["checks"]}
    assert checks["source-freshness:tbankrot.ru"]["ok"] is True
    assert checks["source-coverage:tbankrot.ru"]["ok"] is False


def test_phase3_health_fails_when_configured_source_is_missing() -> None:
    factory = _factory()
    now = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
    with factory() as session:
        session.add(
            MapDataset(
                version="healthy-r6-bundle-s3",
                status="ready",
                is_current=True,
                point_count=100,
                tile_count=200,
                created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                published_at=(now - timedelta(minutes=30)).replace(tzinfo=None),
            )
        )
        _healthy_source(session, now)
        session.commit()

        health = build_phase3_health(
            session,
            now=now,
            expected_sources={"torgi.gov.ru", "missing.example"},
        )

    assert health["healthy"] is False
    check = next(item for item in health["checks"] if item["name"] == "source-health-present")
    assert check["missing_sources"] == ["missing.example"]



def test_phase3_health_keeps_legacy_source_history_out_of_critical_checks() -> None:
    factory = _factory()
    now = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
    with factory() as session:
        session.add(
            MapDataset(
                version="healthy-r6-bundle-s3",
                status="ready",
                is_current=True,
                point_count=100,
                tile_count=200,
                created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                published_at=(now - timedelta(minutes=30)).replace(tzinfo=None),
            )
        )
        _healthy_source(session, now)
        legacy = LotSyncRun(
            id="legacy-run",
            triggered_by="historical",
            trigger_type="manual_full",
            status="success",
            total_sources=1,
            started_at=(now - timedelta(days=40)).replace(tzinfo=None),
            finished_at=(now - timedelta(days=40)).replace(tzinfo=None),
        )
        session.add(legacy)
        session.flush()
        session.add(
            LotSyncSourceRun(
                sync_run_id=legacy.id,
                source_system="ГИС Торги",
                status="success",
                complete_source_run=True,
                items_seen=10,
                started_at=legacy.started_at,
                finished_at=legacy.finished_at,
            )
        )
        session.commit()

        health = build_phase3_health(
            session,
            now=now,
            expected_sources={"torgi.gov.ru"},
        )

    assert health["healthy"] is True
    assert health["summary"]["source_count"] == 1
    assert health["summary"]["legacy_source_count"] == 1
    checks = {item["name"]: item for item in health["checks"]}
    assert checks["source-health-present"]["legacy_sources"] == ["ГИС Торги"]
    assert "source-freshness:ГИС Торги" not in checks
