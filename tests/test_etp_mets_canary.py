"""Admin-only M-ETS canary: no accidental import or category confusion."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bankrotai.domain import NormalizedLot
from bankrotai.services.etp_mets_canary import (
    create_mets_canary_router, validate_mets_canary_lot,
)

def _lot(**overrides):
    data = dict(
        external_id="243003-1", source="m-ets.ru", source_system="m-ets.ru",
        title="Жилой дом 78 м²", description="Земельный участок",
        category="house", region_slug=None, region_name="Московская область",
        address=None, cadastral_number="50:18:0080111:1897",
        vin=None, area=None, start_price=5948100.0, current_price=None,
        auction_status="scheduled", lot_url="https://m-ets.ru/243003-1",
        source_url="https://m-ets.ru/243003-1",
        detail_level="public_detail", raw_data={"evidence_scope": "pilot_non_exhaustive"},
    )
    data.update(overrides)
    return NormalizedLot(**data)


def _test_client(actor=None):
    app = FastAPI()
    def require_admin():
        return actor or SimpleNamespace(id="tester", username="testadmin", role="admin")
    app.include_router(create_mets_canary_router(require_admin))
    return TestClient(app)


def test_safe_real_estate_card_accepted():
    validate_mets_canary_lot(_lot())


@pytest.mark.parametrize("kw", [
    {"category": "receivable"},
    {"category": "car"},
    {"cadastral_number": None},
    {"auction_status": "unknown"},
    {"start_price": None},
    {"source_system": "lot-online.ru"},
    {"lot_url": "https://evil.example/243003-1"},
])
def test_uncertain_lots_fail_before_write(kw):
    with pytest.raises(ValueError):
        validate_mets_canary_lot(_lot(**kw))


def test_import_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ETP_METS_CANARY_IMPORT_ENABLED", raising=False)
    with _test_client() as client:
        r = client.post("/api/etp/pilots/m-ets.ru/import/243003-1")
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"]


def test_preview_returns_non_exhaustive_source_evidence(monkeypatch):
    from bankrotai.connectors.registry.mets_public import MetsConnector
    async def fake_preview(self, *, max_lots=5):
        assert max_lots == 1
        return {
            "source": "m-ets.ru", "lots": [{"external_id": "243003-1"}],
            "complete_source_inventory": False, "safe_for_archival": False,
            "written_to_database": False,
        }
    monkeypatch.setattr(MetsConnector, "preview", fake_preview)
    monkeypatch.delenv("ETP_METS_CANARY_IMPORT_ENABLED", raising=False)
    with _test_client() as client:
        r = client.get("/api/etp/pilots/m-ets.ru/preview")
    assert r.status_code == 200
    assert r.json()["production_import_enabled"] is False
    assert r.json()["complete_source_inventory"] is False
    assert r.json()["written_to_database"] is False


def test_bad_pilot_id_is_rejected():
    with _test_client() as client:
        r = client.post("/api/etp/pilots/m-ets.ru/import/not-a-lot")
    assert r.status_code in {403, 422}
