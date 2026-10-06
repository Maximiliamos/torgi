from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import AuthenticatedUser
from bankrotai.core import utc_now
from bankrotai.db import AppSetting, Base, BackgroundTaskState, GeoFailure, LotGeoSnapshot, LotSyncRun, LotSyncSourceRun, MapDataset, ProcessedLot


def test_operations_progress_reports_search_and_geocoding_counts(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session
            session.commit()

    with scope() as session:
        mapped = ProcessedLot(
            external_id="mapped",
            source="test",
            source_system="test",
            title="Mapped",
            description="",
            category="land",
            address="Москва, Тверская 1",
            auction_status="active",
            current_geo_lat=55.75,
            current_geo_lon=37.61,
            current_geo_source="photon",
            current_geo_confidence="high",
        )
        session.add(mapped)
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=mapped.id,
                geo_source="photon",
                geo_method="address",
                geo_confidence="high",
                centroid_lat=55.75,
                centroid_lon=37.61,
            )
        )
        session.add(
            ProcessedLot(
                external_id="pending",
                source="test",
                source_system="test",
                title="Pending",
                description="",
                category="land",
                address="Москва, Тверская 2",
                auction_status="active",
            )
        )
        session.add(LotSyncRun(
            id="sync-prev", trigger_type="scheduled_full", status="success", total_sources=1,
            created_at=utc_now() - timedelta(hours=2),
            started_at=utc_now() - timedelta(hours=2),
            finished_at=utc_now() - timedelta(hours=1, minutes=50),
        ))
        session.add(
            LotSyncSourceRun(
                sync_run_id="sync-prev",
                source_system="torgi-russia.ru",
                status="success",
                complete_source_run=True,
                pages_scanned=10,
                items_seen=1000,
                started_at=utc_now() - timedelta(hours=2),
                finished_at=utc_now() - timedelta(hours=1, minutes=50),
            )
        )
        session.add(LotSyncRun(id="sync-1", trigger_type="manual", status="running", total_sources=1))
        session.add(
            LotSyncSourceRun(
                sync_run_id="sync-1",
                source_system="torgi-russia.ru",
                status="running",
                pages_scanned=3,
                items_seen=120,
                checkpoint_json={"progress_current": 2, "progress_total": 4, "current_category": "Регион 2 из 4"},
            )
        )
        session.add(
            BackgroundTaskState(
                task_id="geo-1",
                task_type="geocoding",
                status="running",
                started_at=datetime(2026, 9, 11, 8, 0, 0),
                progress_json={"queued": 10, "processed": 4, "geocoded": 3, "failed": 1, "percent": 88},
            )
        )
        session.add(BackgroundTaskState(
            task_id="geo-previous", task_type="geocoding", status="completed",
            result_json={"processed": 100, "duration_seconds": 50},
            created_at=utc_now() - timedelta(hours=1),
        ))
        session.add(AppSetting(key="source_paused:tbankrot.ru", value="true"))
        session.add(MapDataset(
            version="p4-test-s3",
            status="ready",
            is_current=True,
            point_count=39705,
            tile_count=64588,
            published_at=utc_now() - timedelta(minutes=20),
        ))

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api.settings, "api_read_only", True)
    api.app.dependency_overrides[api.require_user] = lambda: AuthenticatedUser(id=1, username="reader", role="reader")
    try:
        response = TestClient(api.app).get("/api/operations/progress")
    finally:
        api.app.dependency_overrides.pop(api.require_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["sync"]["sources"][0]["percent"] == 50.0
    assert payload["sync"]["sources"][0]["current_category"] == "Регион 2 из 4"
    assert payload["geocoding"]["total"] == 2
    assert payload["geocoding"]["geocoded"] == 1
    assert payload["geocoding"]["remaining"] == 1
    assert payload["geocoding"]["eligible_now"] == 1
    assert payload["geocoding"]["waiting_for_retry"] == 0
    assert payload["geocoding"]["task"]["progress"]["processed"] == 4
    assert payload["geocoding"]["rate_per_second"] == 2.0
    assert payload["geocoding"]["eta_seconds"] == 1
    assert payload["geocoding"]["expected_completion_at"] is not None
    assert payload["geocoding"]["paused"] is False
    assert payload["summary"]["sources"]["ready"] == 1
    assert payload["summary"]["sources"]["total"] == 1
    assert payload["summary"]["sources"]["paused"] == 1
    active_source = next(
        item for item in payload["summary"]["sources"]["items"]
        if item["source_system"] == "torgi-russia.ru"
    )
    assert active_source["circuit_state"] == "closed"
    assert active_source["next_retry_at"] is None
    assert active_source["consecutive_operational_failures"] == 0
    assert active_source["network_fingerprint"] == {}
    assert payload["summary"]["sources"]["items"][-1]["source_system"] == "tbankrot.ru"
    assert payload["summary"]["sources"]["items"][-1]["paused"] is True
    assert payload["summary"]["map"]["point_count"] == 39705
    assert payload["summary"]["map"]["status"] == "ready"
    assert payload["summary"]["last_update_at"] is not None


def test_geocoding_pause_controls_require_admin_and_persist(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api.settings, "api_read_only", True)
    client = TestClient(api.app)
    api.app.dependency_overrides[api.require_user] = lambda: AuthenticatedUser(id=1, username="reader", role="reader")
    try:
        assert client.post("/api/operations/geocoding/pause").status_code == 403
    finally:
        api.app.dependency_overrides.pop(api.require_user, None)

    api.app.dependency_overrides[api.require_user] = lambda: AuthenticatedUser(id=2, username="admin", role="admin")
    try:
        paused = client.post("/api/operations/geocoding/pause")
        assert paused.status_code == 200
        with scope() as session:
            assert session.scalar(select(AppSetting.value).where(AppSetting.key == "geocoding_paused")) == "true"
        resumed = client.post("/api/operations/geocoding/resume")
        assert resumed.status_code == 200
        with scope() as session:
            assert session.scalar(select(AppSetting.value).where(AppSetting.key == "geocoding_paused")) == "false"
    finally:
        api.app.dependency_overrides.pop(api.require_user, None)


def test_geocoding_campaign_elapsed_time_excludes_idle_gaps() -> None:
    from bankrotai.services.geo_backfill import geocoding_progress

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    campaign = "geo-20260917-120000"
    with Session(engine) as session:
        session.add_all([
            BackgroundTaskState(
                task_id=f"{campaign}-001",
                task_type="geocoding",
                status="completed",
                started_at=datetime(2026, 9, 17, 8, 0, 0),
                finished_at=datetime(2026, 9, 17, 8, 2, 0),
                result_json={"processed": 100, "duration_seconds": 120},
            ),
            BackgroundTaskState(
                task_id=f"{campaign}-002",
                task_type="geocoding",
                status="completed",
                started_at=datetime(2026, 9, 17, 12, 0, 0),
                finished_at=datetime(2026, 9, 17, 12, 3, 0),
                result_json={"processed": 100, "duration_seconds": 180},
            ),
        ])
        session.commit()
        progress = geocoding_progress(session)

    assert progress["elapsed_seconds"] == 300
    assert progress["estimated_total_seconds"] == 300


def test_geocoding_progress_does_not_claim_eta_for_future_retry(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session
            session.commit()

    with scope() as session:
        pending = ProcessedLot(
            external_id="waiting-retry",
            source="test",
            source_system="test",
            title="Waiting retry",
            description="",
            category="land",
            address="Москва, Тверская 9",
            auction_status="active",
        )
        session.add(pending)
        session.flush()
        session.add(
            GeoFailure(
                lot_id=pending.id,
                status="queued",
                attempt_count=3,
                error_message="no coordinates",
                last_failed_at=utc_now(),
                next_retry_at=utc_now() + timedelta(hours=6),
            )
        )
        session.add(
            BackgroundTaskState(
                task_id="geo-rate-sample",
                task_type="geocoding",
                status="completed",
                result_json={"processed": 100, "duration_seconds": 50},
                created_at=utc_now() - timedelta(minutes=5),
            )
        )

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api.settings, "api_read_only", True)
    api.app.dependency_overrides[api.require_user] = lambda: AuthenticatedUser(id=1, username="reader", role="reader")
    try:
        response = TestClient(api.app).get("/api/operations/progress")
    finally:
        api.app.dependency_overrides.pop(api.require_user, None)

    assert response.status_code == 200
    geo = response.json()["geocoding"]
    assert geo["total"] == 1
    assert geo["geocoded"] == 0
    assert geo["remaining"] == 1
    assert geo["actionable_remaining"] == 1
    assert geo["eligible_now"] == 0
    assert geo["waiting_for_retry"] == 1
    assert geo["next_retry_at"] is not None
    assert geo["rate_per_second"] == 2.0
    assert geo["eta_seconds"] is None
    assert geo["expected_completion_at"] is None
