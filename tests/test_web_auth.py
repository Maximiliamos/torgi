from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import authenticate_user, hash_password, upsert_user, verify_password
from bankrotai.db import Base, ProcessedLot


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return scope


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("wrong password", first) is False


def test_user_authentication_and_password_rotation() -> None:
    scope = _session_factory()
    with scope() as session:
        user = upsert_user(session, "Reader", "first secure password", role="reader")
        original_version = user.token_version
    with scope() as session:
        assert authenticate_user(session, "reader", "first secure password") is not None
        rotated = upsert_user(session, "reader", "second secure password", role="reader")
        assert rotated.token_version == original_version + 1
    with scope() as session:
        assert authenticate_user(session, "reader", "first secure password") is None
        assert authenticate_user(session, "reader", "second secure password") is not None


def test_read_only_api_requires_user_session_and_never_accepts_user_id(monkeypatch) -> None:
    scope = _session_factory()
    with scope() as session:
        upsert_user(session, "reader", "a sufficiently secure password", role="reader")
        session.add(ProcessedLot(
            external_id="web-auth-lot",
            source="test",
            source_system="test",
            title="Authenticated lot",
            description="",
            category="land",
            region_slug="yaroslavl",
            auction_status="active",
        ))
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(api.settings, "public_api_key", "service-key-that-is-long-enough")
    monkeypatch.setattr(api.settings, "auth_session_secret", "session-secret-" * 4)
    monkeypatch.setattr(
        api.settings,
        "database_url",
        "postgresql+psycopg://bankrotai:password@ep-example-pooler.eu.neon.tech/db"
        "?sslmode=require&channel_binding=require",
    )
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)
    client = TestClient(api.app, base_url="https://testserver")
    service_headers = {"X-API-Key": api.settings.public_api_key}

    assert client.get("/api/lots", headers=service_headers).status_code == 401
    login = client.post(
        "/api/auth/login",
        headers=service_headers,
        json={"username": "reader", "password": "a sufficiently secure password"},
    )
    assert login.status_code == 200
    assert login.json()["username"] == "reader"
    assert "httponly" in login.headers["set-cookie"].lower()
    assert client.get("/api/lots", headers=service_headers).status_code == 200
    assert client.post(
        "/api/lots/1/split",
        headers=service_headers,
        json={"reason": "reader must not modify duplicate groups"},
    ).status_code == 403
    assert client.post("/api/regions/yaroslavl/sync", headers=service_headers).status_code == 404
    assert "user_id" not in api.ParticipationChecklistRequest.model_fields
    with pytest.raises(ValidationError):
        api.ParticipationChecklistRequest(user_id="attacker")
