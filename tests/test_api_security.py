from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bankrotai import api, core


client = TestClient(api.app)


def _configure_production(monkeypatch, api_key: str | None) -> None:
    monkeypatch.setattr(api.settings, "app_env", "production")
    monkeypatch.setattr(api.settings, "public_api_key", api_key)
    monkeypatch.setattr(
        api.settings,
        "database_url",
        "postgresql+psycopg://bankrotai:strong-database-password@postgres:5432/bankrotai",
    )
    monkeypatch.setattr(api.settings, "redis_url", "redis://:strong-redis-password@redis:6379/0")
    monkeypatch.setattr(api, "_consume_rate_limit", lambda _client_id: True)


def test_production_rejects_missing_api_key_configuration(monkeypatch) -> None:
    _configure_production(monkeypatch, None)
    response = client.get("/")
    assert response.status_code == 503
    assert response.json()["detail"] == "API security configuration is incomplete"


def test_production_protects_get_requests(monkeypatch) -> None:
    api_key = "a-secure-api-key-with-24-characters"
    _configure_production(monkeypatch, api_key)
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/", headers={"X-API-Key": api_key}).status_code == 200


def test_health_endpoints_remain_public(monkeypatch) -> None:
    _configure_production(monkeypatch, None)
    assert client.get("/health/live").status_code == 200


def test_production_settings_reject_default_or_missing_secrets() -> None:
    settings = core.AppSettings(
        app_env="production",
        database_url="postgresql+psycopg://postgres:postgres@postgres:5432/bankrotai",
        redis_url="redis://redis:6379/0",
    )
    errors = settings.production_configuration_errors()
    assert any("BANKROTAI_API_KEY" in error for error in errors)
    assert any("postgres/postgres" in error for error in errors)
    assert any("Redis password" in error for error in errors)


def test_secret_app_settings_cannot_be_persisted() -> None:
    assert core.get_app_setting("openai_api_key", "from-environment") == "from-environment"
    with pytest.raises(ValueError, match="secret manager"):
        core.set_app_setting("openai_api_key", "must-not-enter-database")
