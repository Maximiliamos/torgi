from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from bankrotai import api
from bankrotai.api_models import LotIssueReportRequest
from bankrotai.db import Base, LotIssueReport


def test_report_error_post_is_allowed_in_read_only_mvp() -> None:
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/lots/123/report-error"),
    )
    assert api._is_read_only_mvp_path(request) is True


def test_lot_issue_report_request_is_bounded() -> None:
    assert LotIssueReportRequest(content="Неверная цена").content == "Неверная цена"
    with pytest.raises(ValidationError):
        LotIssueReportRequest(content="")
    with pytest.raises(ValidationError):
        LotIssueReportRequest(content="x" * 5001)


def test_lot_issue_report_table_is_registered() -> None:
    assert "lot_issue_reports" in Base.metadata.tables
    table = Base.metadata.tables["lot_issue_reports"]
    assert set(table.c.keys()) == {"id", "lot_id", "user_id", "content", "created_at"}
    assert LotIssueReport.__tablename__ == "lot_issue_reports"


def test_report_lot_id_is_historical_not_cascading_foreign_key() -> None:
    assert not LotIssueReport.__table__.c.lot_id.foreign_keys


def test_authenticated_report_persists_after_lot_deletion(monkeypatch) -> None:
    from contextlib import contextmanager

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from bankrotai.auth import AuthenticatedUser
    from bankrotai.db import ProcessedLot
    from bankrotai.services import lot_issue_report_api as report_api

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        ProcessedLot.__table__.create(connection)
        LotIssueReport.__table__.create(connection)
        connection.execute(
            ProcessedLot.__table__.insert().values(
                id=77, external_id="77", title="Test lot",
                category="commercial", source="test", source_system="test",
            )
        )

    @contextmanager
    def isolated_session_scope():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(report_api, "session_scope", isolated_session_scope)
    monkeypatch.setattr(report_api, "log_action", lambda *_args, **_kwargs: None)

    def authenticated_reader() -> AuthenticatedUser:
        return AuthenticatedUser(id=7, username="reader", role="reader")

    app = FastAPI()
    app.include_router(report_api.create_lot_issue_report_router(authenticated_reader))
    with TestClient(app) as client:
        saved = client.post("/api/lots/77/report-error", json={"content": "  Wrong price  "})
        assert saved.status_code == 201
        assert saved.json()["content"] == "Wrong price"
        assert saved.json()["lot_id"] == 77

        with Session(engine) as session, session.begin():
            existing_lot = session.get(ProcessedLot, 77)
            assert existing_lot is not None
            session.delete(existing_lot)

        with Session(engine) as session:
            report = session.scalar(select(LotIssueReport))
            assert report is not None
            assert report.lot_id == 77
            assert report.content == "Wrong price"
            assert report.user_id == "7"

        missing = client.post("/api/lots/77/report-error", json={"content": "Missing"})
        assert missing.status_code == 404
        blank = client.post("/api/lots/77/report-error", json={"content": "  "})
        assert blank.status_code == 422

    def deny_anonymous() -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    denied_app = FastAPI()
    denied_app.include_router(report_api.create_lot_issue_report_router(deny_anonymous))
    with TestClient(denied_app) as client:
        unauthorized = client.post("/api/lots/77/report-error", json={"content": "Denied"})
        assert unauthorized.status_code == 401

    engine.dispose()



def test_readonly_report_export_uses_source_lot_for_dates(monkeypatch) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    from bankrotai.services import lot_issue_report_export as report_export

    report = SimpleNamespace(
        id=9,
        lot_id=77,
        user_id="3",
        created_at=datetime(2026, 11, 9, 0, 30),
        content="Нет даты проведения",
    )
    # Deliberately omit application_deadline/auction_at: these fields do not
    # exist on ProcessedLot and caused the production exporter to crash.
    lot = SimpleNamespace(
        external_id="test:77",
        source="test",
        source_system="test.ru",
        title="Test lot",
        description="Description",
        region_code="50",
        region_name="Московская область",
        address="г. Электросталь",
        cadastral_number="50:46:0000000:77",
        start_price=None,
        current_price=None,
        auction_status="closed",
        is_archived=False,
        closed_at=None,
        current_geo_lat=55.78,
        current_geo_lon=38.44,
        current_geo_source="photon",
        current_geo_confidence="high",
        lot_url="https://example.test/77",
        source_url="https://example.test/77",
    )
    source_lot = SimpleNamespace(
        source_status="Торги завершены",
        application_start_at=datetime(2026, 10, 1, 9, 0),
        application_deadline=datetime(2026, 10, 20, 18, 0),
        auction_at=datetime(2026, 10, 23, 10, 0),
        auction_timezone="Europe/Moscow",
        is_active=False,
        is_archived=True,
        archive_reason="closed_upstream",
        raw_data={
            "detail_enrichment_status": "success",
            "detail_dates_verified": True,
        },
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalars(self, _statement):
            return [report]

        def get(self, model, identity):
            assert model is report_export.ProcessedLot
            assert identity == 77
            return lot

        def scalar(self, _statement):
            return source_lot

    monkeypatch.setattr(report_export, "SessionLocal", lambda: FakeSession())

    payload = report_export.export_lot_issue_reports(limit=10)

    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["application_deadline"] == "2026-10-20T18:00:00"
    assert item["auction_at"] == "2026-10-23T10:00:00"
    assert item["source_status"] == "Торги завершены"
    assert item["current_geo_lat"] == 55.78
    assert item["raw_debug"]["detail_dates_verified"] is True
