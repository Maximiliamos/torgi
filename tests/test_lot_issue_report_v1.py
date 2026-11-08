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
