"""Authenticated catalogue pilot routes never write or expose unknown platforms."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bankrotai.services.etp_direct_catalogue_api import create_etp_pilot_catalogue_router


def test_pilot_registry_only_shows_nonproduction_sources():
    app = FastAPI()
    app.include_router(create_etp_pilot_catalogue_router(
        lambda: SimpleNamespace(id=1, role="admin")
    ))
    with TestClient(app) as client:
        result = client.get("/api/etp/pilots/sources")
    assert result.status_code == 200
    data = result.json()
    assert len(data["items"]) == 14
    assert all(item["production_ingestion_enabled"] is False for item in data["items"])
    assert data["all_new_etps_read_only"] is True


def test_unknown_or_unapproved_pilot_never_hits_network():
    app = FastAPI()
    app.include_router(create_etp_pilot_catalogue_router(
        lambda: SimpleNamespace(id=1, role="admin")
    ))
    with TestClient(app) as client:
        response = client.get("/api/etp/pilots/unknown.ru/preview")
        oversized = client.get("/api/etp/pilots/nistp.ru/preview?limit=100")
    assert response.status_code == 404
    assert oversized.status_code == 422


def test_public_catalogue_preview_is_explicitly_unverified(monkeypatch):
    from bankrotai.connectors.registry.direct_etp_pilots import DirectEtpPilotConnector
    async def fake_preview(self, *, limit=10):
        assert limit == 1
        return {
            "source": self.source_id, "database_writes": 0,
            "ready_for_ingestion": False, "safe_for_archival": False,
            "complete_source_inventory": False, "candidates": [],
        }
    monkeypatch.setattr(DirectEtpPilotConnector, "preview", fake_preview)
    app = FastAPI()
    app.include_router(create_etp_pilot_catalogue_router(
        lambda: SimpleNamespace(id=1, role="admin")
    ))
    with TestClient(app) as client:
        response = client.get("/api/etp/pilots/nistp.ru/preview?limit=1")
    assert response.status_code == 200
    result = response.json()
    assert result["database_writes"] == 0
    assert result["safe_for_archival"] is False
