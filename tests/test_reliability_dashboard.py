from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.services import reliability_api
from bankrotai.auth import AuthenticatedUser
from bankrotai.db import AppSetting, Base, LotSyncRun, LotSyncSourceRun, ProcessedLot
from bankrotai.services.reliability import (
    _current_data_quality,
    _disk_status,
    build_reliability_sources,
    configured_source_ids,
)


def _engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _run(session: Session, run_id: str, *, source: str, status: str, when: datetime, error: str | None = None):
    session.add(
        LotSyncRun(
            id=run_id,
            trigger_type="test",
            status=status,
            total_sources=1,
            created_at=when - timedelta(minutes=1),
            started_at=when - timedelta(minutes=1),
            finished_at=when,
        )
    )
    session.add(
        LotSyncSourceRun(
            sync_run_id=run_id,
            source_system=source,
            status=status,
            complete_source_run=status == "success",
            pages_scanned=1,
            items_seen=10,
            items_inserted=1,
            items_updated=2,
            items_failed=0,
            error_message=error,
            started_at=when - timedelta(minutes=1),
            finished_at=when,
        )
    )


def test_reliability_sources_collapse_alias_and_hide_closed_historical_error() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 19, 18, 0, 0)
    with Session(engine) as session:
        _run(
            session,
            "old-failure",
            source="ГИС Торги",
            status="failed",
            when=now - timedelta(hours=2),
            error="old timeout",
        )
        _run(
            session,
            "new-success",
            source="torgi.gov.ru",
            status="success",
            when=now - timedelta(minutes=30),
        )
        session.commit()

        rows = build_reliability_sources(session, now=now)

    source = next(item for item in rows if item["canonical_id"] == "torgi.gov.ru")
    assert source["display_name_ru"] == "ГИС Торги"
    assert "ГИС Торги" in source["aliases"]
    assert source["current_error"] is None
    assert source["last_historical_error"] == "old timeout"
    assert source["overall_status"] == "healthy"


def test_paused_configured_source_remains_in_configured_denominator() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AppSetting(key="source_paused:tbankrot.ru", value="true"))
        session.commit()
        rows = build_reliability_sources(session, now=datetime(2026, 11, 19, 18, 0, 0))

    configured = [item for item in rows if item["configured"]]
    assert len(configured) == len(configured_source_ids())
    tbankrot = next(item for item in configured if item["canonical_id"] == "tbankrot.ru")
    assert tbankrot["paused"] is True
    assert tbankrot["overall_status"] == "paused"


def test_current_data_quality_excludes_archived_and_ended_rows() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            ProcessedLot(
                external_id="live-missing",
                source="test",
                source_system="test",
                title="Живой",
                description="",
                category="land",
                auction_status="active",
                address=None,
                cadastral_number=None,
                region_code=None,
                lot_url=None,
                source_url=None,
            ),
            ProcessedLot(
                external_id="archived-missing",
                source="test",
                source_system="test",
                title="Архив",
                description="",
                category="land",
                auction_status="active",
                is_archived=True,
                address=None,
                cadastral_number=None,
            ),
            ProcessedLot(
                external_id="closed-missing",
                source="test",
                source_system="test",
                title="Закрыт",
                description="",
                category="land",
                auction_status="closed",
                address=None,
                cadastral_number=None,
            ),
        ])
        session.commit()
        snapshot = _current_data_quality(session)

    assert snapshot["current_population"] == 1
    assert snapshot["missing_address"]["count"] == 1
    assert snapshot["missing_cadastre"]["count"] == 1
    assert snapshot["missing_address"]["percent"] == 100.0
    assert snapshot["historical_total"] == 3


def test_disk_status_has_warning_band_between_recommended_and_critical() -> None:
    assert _disk_status({"disk": {"free_gb": 188.4}})["status"] == "ok"
    assert _disk_status({"disk": {"free_gb": 20.0}})["status"] == "warning"
    assert _disk_status({"disk": {"free_gb": 12.0}})["status"] == "critical"
    assert _disk_status({"disk": {"free_gb": None}})["status"] == "unknown"


def test_reliability_get_endpoints_are_available_in_read_only_mode(monkeypatch) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(reliability_api, "build_reliability_summary", lambda *args, **kwargs: {"schema_version": 1})
    monkeypatch.setattr(reliability_api, "build_reliability_sources", lambda *args, **kwargs: [])
    monkeypatch.setattr(reliability_api, "reliability_map_delivery", lambda *args, **kwargs: {"status": "ok"})

    api.app.dependency_overrides[api.require_user] = lambda: AuthenticatedUser(
        id=1,
        username="reader",
        role="reader",
    )
    try:
        client = TestClient(api.app)
        assert client.get("/api/reliability/summary").status_code == 200
        assert client.get("/api/reliability/sources").status_code == 200
        assert client.get("/api/reliability/map-delivery").status_code == 200
    finally:
        api.app.dependency_overrides.pop(api.require_user, None)
