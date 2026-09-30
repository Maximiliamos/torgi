from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from bankrotai import api
from bankrotai.auth import AuthenticatedUser
from bankrotai.core import utc_now
from bankrotai.db import AppSetting, Base, LotSyncRun, LotSyncSourceRun


def _request(method: str, path: str) -> Request:
    return Request({
        "type": "http",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
    })


def _scope_factory():
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

    return scope


def test_read_only_mvp_allows_only_the_bounded_tbankrot_auth_contract() -> None:
    assert api._is_read_only_mvp_path(_request("GET", "/api/tbankrot/status"))
    assert api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/auth/start"))
    assert api._is_read_only_mvp_path(_request("GET", "/api/tbankrot/auth/123/frame"))
    assert api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/auth/123/action"))
    assert api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/auth/123/verify"))
    assert api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/auth/123/close"))
    assert api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/sync"))
    assert not api._is_read_only_mvp_path(_request("POST", "/api/tbankrot/auth/123/navigate"))
    assert not api._is_read_only_mvp_path(_request("PUT", "/api/tbankrot/auth/123/action"))


def test_tbankrot_browser_action_contract_rejects_arbitrary_commands() -> None:
    assert api.TBankrotBrowserActionRequest(type="click", x=10, y=20).type == "click"
    with pytest.raises(ValidationError):
        api.TBankrotBrowserActionRequest(type="navigate", value="https://example.com")
    with pytest.raises(ValidationError):
        api.TBankrotBrowserActionRequest(type="text", value="x" * 4001)


def test_verified_tbankrot_session_queues_only_isolated_source(monkeypatch) -> None:
    queued: list[dict[str, str]] = []

    async def verify(_settings, session_id: str):
        assert session_id == "browser-1"
        return {"ok": True, "state": "authenticated", "captured_at": "2026-09-30T01:00:00Z"}

    monkeypatch.setattr(api, "verify_tbankrot_browser_session", verify)
    monkeypatch.setattr(
        api,
        "schedule_nationwide_lot_sync",
        lambda **kwargs: queued.append(kwargs) or "sync-tbankrot-1",
    )
    actor = AuthenticatedUser(id=7, username="admin", role="admin")

    result = asyncio.run(api.verify_tbankrot_auth("browser-1", actor))

    assert result["sync"] == {"status": "queued", "task_id": "sync-tbankrot-1"}
    assert queued == [{
        "triggered_by": "tbankrot-auth:7",
        "mode": "source:tbankrot.ru",
    }]


def test_unverified_tbankrot_session_never_queues_sync(monkeypatch) -> None:
    async def verify(_settings, _session_id: str):
        return {"ok": False, "state": "auth_required"}

    monkeypatch.setattr(api, "verify_tbankrot_browser_session", verify)
    monkeypatch.setattr(
        api,
        "schedule_nationwide_lot_sync",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sync must not be queued")),
    )

    result = asyncio.run(
        api.verify_tbankrot_auth(
            "browser-1",
            AuthenticatedUser(id=7, username="admin", role="admin"),
        )
    )
    assert result == {"ok": False, "state": "auth_required"}


def test_manual_tbankrot_sync_requires_live_saved_session_probe(monkeypatch) -> None:
    async def denied(_settings):
        return {"ok": False, "state": "auth_required", "saved": True}

    monkeypatch.setattr(api, "probe_tbankrot_saved_session", denied)
    actor = AuthenticatedUser(id=7, username="admin", role="admin")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.sync_tbankrot(actor))
    assert exc.value.status_code == 428

    async def allowed(_settings):
        return {"ok": True, "state": "authenticated", "saved": True}

    queued: list[dict[str, str]] = []
    monkeypatch.setattr(api, "probe_tbankrot_saved_session", allowed)
    monkeypatch.setattr(
        api,
        "schedule_nationwide_lot_sync",
        lambda **kwargs: queued.append(kwargs) or "sync-manual-1",
    )
    assert asyncio.run(api.sync_tbankrot(actor)) == {
        "status": "queued",
        "task_id": "sync-manual-1",
    }
    assert queued == [{
        "triggered_by": "tbankrot-manual:7",
        "mode": "source:tbankrot.ru",
    }]


def test_newer_saved_auth_clears_older_access_limited_failure(monkeypatch) -> None:
    scope = _scope_factory()
    failure_at = utc_now() - timedelta(hours=2)
    with scope() as session:
        session.add(AppSetting(key="source_paused:tbankrot.ru", value="true"))
        session.add(LotSyncRun(
            id="old-tbankrot",
            trigger_type="manual_source_full",
            status="failed",
            total_sources=1,
            created_at=failure_at,
            started_at=failure_at,
            finished_at=failure_at,
        ))
        session.add(LotSyncSourceRun(
            sync_run_id="old-tbankrot",
            source_system="tbankrot.ru",
            status="failed",
            error_message="TBankrot access_limited: login required",
            started_at=failure_at,
            finished_at=failure_at,
        ))

    async def broker(_settings):
        return {
            "saved": True,
            "captured_at": utc_now().isoformat().replace("+00:00", "Z"),
            "active_session_id": None,
            "browser_ready": True,
        }

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api, "tbankrot_broker_status", broker)
    result = asyncio.run(api.get_tbankrot_status(
        AuthenticatedUser(id=1, username="reader", role="reader")
    ))

    assert result["state"] == "ready"
    assert result["paused_from_automatic_sync"] is True
    assert result["saved_session"] is True
    assert "cookies" not in result


def test_missing_saved_tbankrot_session_is_reported_as_auth_required(monkeypatch) -> None:
    scope = _scope_factory()

    async def broker(_settings):
        return {
            "saved": False,
            "captured_at": None,
            "active_session_id": None,
            "browser_ready": True,
        }

    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api, "tbankrot_broker_status", broker)
    result = asyncio.run(api.get_tbankrot_status(
        AuthenticatedUser(id=1, username="reader", role="reader")
    ))
    assert result["state"] == "auth_required"
