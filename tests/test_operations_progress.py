from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import AuthenticatedUser
from bankrotai.core import utc_now
from bankrotai.db import AppSetting, Base, BackgroundTaskState, LotGeoSnapshot, LotSyncRun, LotSyncSourceRun, ProcessedLot


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
                progress_json={"queued": 10, "processed": 4, "geocoded": 3, "failed": 1, "percent": 88},
            )
        )
        session.add(BackgroundTaskState(
            task_id="geo-previous", task_type="geocoding", status="completed",
            result_json={"processed": 100, "duration_seconds": 50},
            created_at=utc_now() - timedelta(hours=1),
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
    assert payload["geocoding"]["task"]["progress"]["processed"] == 4
    assert payload["geocoding"]["rate_per_second"] == 2.0
    assert payload["geocoding"]["eta_seconds"] == 1
    assert payload["geocoding"]["paused"] is False


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
