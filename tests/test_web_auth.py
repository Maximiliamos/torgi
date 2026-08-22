from __future__ import annotations

import asyncio
from contextlib import contextmanager
import threading
import time

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import AuthenticatedUser, authenticate_user, hash_password, upsert_user, verify_password
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
    monkeypatch.setattr(api, "read_session_scope", scope)
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
    service_headers = {
        "X-API-Key": api.settings.public_api_key,
        "X-Request-ID": "test-request-correlation",
    }

    assert client.get("/api/lots", headers=service_headers).status_code == 401
    login = client.post(
        "/api/auth/login",
        headers=service_headers,
        json={"username": "reader", "password": "a sufficiently secure password"},
    )
    assert login.status_code == 200
    assert login.headers["x-request-id"] == "test-request-correlation"
    assert login.json()["username"] == "reader"
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/auth/me", headers=service_headers).status_code == 200
    assert client.get("/api/lots", headers=service_headers).status_code == 200
    assert client.post(
        "/api/lots/1/split",
        headers=service_headers,
        json={"reason": "reader must not modify duplicate groups"},
    ).status_code == 403
    assert client.post("/api/regions/yaroslavl/sync", headers=service_headers).status_code == 404
    assert client.post("/api/auth/logout", headers=service_headers).status_code == 200
    assert client.get("/api/auth/me", headers=service_headers).status_code == 401
    assert client.post(
        "/api/auth/login",
        headers=service_headers,
        json={"username": "reader", "password": "a sufficiently secure password"},
    ).status_code == 200
    assert client.get("/api/auth/me", headers=service_headers).status_code == 200
    assert "user_id" not in api.ParticipationChecklistRequest.model_fields
    with pytest.raises(ValidationError):
        api.ParticipationChecklistRequest(user_id="attacker")


def test_operator_and_diagnostics_endpoints_require_admin(monkeypatch) -> None:
    scope = _session_factory()
    with scope() as session:
        upsert_user(session, "reader", "a sufficiently secure password", role="reader")
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "read_session_scope", scope)
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "api_read_only", False)
    monkeypatch.setattr(api.settings, "public_api_key", "service-key-that-is-long-enough")
    monkeypatch.setattr(api.settings, "auth_session_secret", "session-secret-" * 4)
    monkeypatch.setattr(
        api.settings,
        "database_url",
        "postgresql+psycopg://bankrotai:password@postgres.test/db?sslmode=require&channel_binding=require",
    )
    monkeypatch.setattr(api.settings, "redis_url", "redis://:password@redis.test:6379/0")
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)
    client = TestClient(api.app, base_url="https://testserver")
    headers = {"X-API-Key": api.settings.public_api_key}

    login = client.post(
        "/api/auth/login",
        headers=headers,
        json={"username": "reader", "password": "a sufficiently secure password"},
    )
    assert login.status_code == 200
    for method, path in (
        ("get", "/api/diagnostics"),
        ("post", "/api/online/torgi-gov/sync"),
        ("get", "/api/tasks/unowned-task"),
        ("post", "/api/regions/yaroslavl/sync"),
        ("get", "/api/regions/yaroslavl/sync-status"),
    ):
        assert getattr(client, method)(path, headers=headers).status_code == 403


def test_authenticated_rate_limit_uses_verified_user_not_proxy_ip(monkeypatch) -> None:
    scope = _session_factory()
    with scope() as session:
        upsert_user(session, "reader", "a sufficiently secure password", role="reader")
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "read_session_scope", scope)
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
    observed: list[str] = []
    monkeypatch.setattr(api, "_consume_rate_limit", lambda client_id: observed.append(client_id) or True)
    client = TestClient(api.app, base_url="https://testserver")
    headers = {
        "X-API-Key": api.settings.public_api_key,
        "CF-Connecting-IP": "2a06:98c0:3600::103",
    }

    assert client.post(
        "/api/auth/login",
        headers=headers,
        json={"username": "reader", "password": "a sufficiently secure password"},
    ).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    assert observed[0] == "ip:2a06:98c0:3600::103"
    assert observed[1] == "user:1"


def test_verified_user_rate_limit_does_not_weaken_anonymous_ip_limit(monkeypatch) -> None:
    class UnavailableRedis:
        def incr(self, _key: str) -> int:
            raise RedisError("unavailable")

    monkeypatch.setattr(api.Redis, "from_url", lambda *_args, **_kwargs: UnavailableRedis())
    monkeypatch.setattr(api.settings, "app_env", "development")
    monkeypatch.setattr(api.settings, "api_rate_limit_per_minute", 2)
    api._rate_limit_hits.clear()

    assert api._consume_rate_limit("ip:203.0.113.10") is True
    assert api._consume_rate_limit("ip:203.0.113.10") is True
    assert api._consume_rate_limit("ip:203.0.113.10") is False

    assert all(api._consume_rate_limit("user:1") for _ in range(20))
    assert api._consume_rate_limit("user:1") is False
    api._rate_limit_hits.clear()


def test_review_status_rejects_empty_or_unknown_values() -> None:
    assert api.ReviewStatusRequest(status=None).status is None
    assert api.ReviewStatusRequest(status="approved").status == "approved"
    with pytest.raises(ValidationError):
        api.ReviewStatusRequest(status="")
    with pytest.raises(ValidationError):
        api.ReviewStatusRequest(status="admin-approved")


def test_stalled_session_database_lookup_does_not_block_liveness(monkeypatch) -> None:
    @contextmanager
    def stalled_scope():
        yield object()

    monkeypatch.setattr(api, "read_session_scope", stalled_scope)

    def stalled_verification(_session, _token: str, _secret: str) -> AuthenticatedUser:
        time.sleep(0.5)
        return AuthenticatedUser(id=1, username="reader", role="reader")

    monkeypatch.setattr(api, "session_scope", stalled_scope)
    monkeypatch.setattr(api, "verify_session_token", stalled_verification)
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(api.settings, "public_api_key", "service-key-that-is-long-enough")
    monkeypatch.setattr(api.settings, "auth_session_secret", "session-secret-" * 4)
    monkeypatch.setattr(api.settings, "database_auth_timeout_seconds", 0.1)
    monkeypatch.setattr(
        api.settings,
        "database_url",
        "postgresql+psycopg://bankrotai:password@ep-example-pooler.eu.neon.tech/db?sslmode=require",
    )
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://testserver",
            cookies={"bankrotai_session": "validly-shaped-test-token"},
        ) as client:
            started = time.perf_counter()
            stalled = asyncio.create_task(client.get(
                "/api/auth/me",
                headers={"X-API-Key": api.settings.public_api_key},
            ))
            await asyncio.sleep(0.02)
            live = await client.get("/health/live")
            assert live.status_code == 200
            assert time.perf_counter() - started < 0.25
            assert (await stalled).status_code == 503

    asyncio.run(exercise())


def test_repeated_stalled_session_lookups_have_bounded_capacity(monkeypatch) -> None:
    release = threading.Event()
    entered = 0
    entered_lock = threading.Lock()

    @contextmanager
    def stalled_scope():
        yield object()

    def stalled_verification(_session, _token: str, _secret: str) -> AuthenticatedUser:
        nonlocal entered
        with entered_lock:
            entered += 1
        release.wait(timeout=1)
        return AuthenticatedUser(id=1, username="reader", role="reader")

    monkeypatch.setattr(api, "session_scope", stalled_scope)
    monkeypatch.setattr(api, "verify_session_token", stalled_verification)
    monkeypatch.setattr(api, "read_session_scope", stalled_scope)
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "api_read_only", True)
    monkeypatch.setattr(api.settings, "public_api_key", "service-key-that-is-long-enough")
    monkeypatch.setattr(api.settings, "auth_session_secret", "session-secret-" * 4)
    monkeypatch.setattr(api.settings, "database_auth_timeout_seconds", 0.05)
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://testserver",
            cookies={"bankrotai_session": "validly-shaped-test-token"},
        ) as client:
            requests = [
                asyncio.create_task(client.get(
                    "/api/auth/me",
                    headers={"X-API-Key": api.settings.public_api_key},
                ))
                for _ in range(api._AUTH_PENDING_LIMIT + 8)
            ]
            await asyncio.sleep(0.01)
            started = time.perf_counter()
            live = await client.get("/health/live")
            assert live.status_code == 200
            assert time.perf_counter() - started < 0.25
            responses = await asyncio.gather(*requests)
            assert all(response.status_code == 503 for response in responses)
            assert entered <= api._AUTH_EXECUTOR_WORKERS

    try:
        asyncio.run(exercise())
    finally:
        release.set()

    acquired = 0
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        while acquired < api._AUTH_EXECUTOR_WORKERS and api._AUTH_EXECUTOR_CAPACITY.acquire(blocking=False):
            acquired += 1
        if acquired == api._AUTH_EXECUTOR_WORKERS:
            break
        for _ in range(acquired):
            api._AUTH_EXECUTOR_CAPACITY.release()
        acquired = 0
        time.sleep(0.01)
    for _ in range(acquired):
        api._AUTH_EXECUTOR_CAPACITY.release()
    assert acquired == api._AUTH_EXECUTOR_WORKERS

    recovered = TestClient(
        api.app,
        base_url="https://testserver",
        cookies={"bankrotai_session": "validly-shaped-test-token"},
    )
    assert recovered.get(
        "/api/auth/me",
        headers={"X-API-Key": api.settings.public_api_key},
    ).status_code == 200
