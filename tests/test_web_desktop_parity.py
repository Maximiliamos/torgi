from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai import api
from bankrotai.auth import upsert_user
from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, ProcessedLot, SourceLot


def _authenticated_client(monkeypatch) -> tuple[TestClient, int]:
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

    with scope() as session:
        upsert_user(session, "reader", "a sufficiently secure password", role="reader")
        lot = ProcessedLot(
            external_id="parity-lot",
            source="test",
            source_system="test",
            title="Лот для веб-инструментов",
            description="",
            category="land",
            region_slug="76",
            region_code="76",
            start_price=Decimal("900000"),
            current_price=Decimal("1000000"),
            auction_status="active",
        )
        session.add(lot)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="parity-lot",
            legacy_processed_lot_id=lot.id,
            title=lot.title,
            category=lot.category,
        )
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=lot.id,
            source_system="test",
            external_id=lot.external_id,
            source_url="https://example.test/lot/parity-lot",
            platform_name="Тестовая ЭТП",
            procedure_number="PROC-76-1",
            raw_data={"photos": [{"url": "https://example.test/photo.jpg"}]},
        ))
        session.add(LotGeoSnapshot(
            lot_id=lot.id,
            geo_source="test",
            geo_method="fixture",
            geo_confidence="high",
            centroid_lat=57.6261,
            centroid_lon=39.8845,
        ))
        lot_id = lot.id

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
    client = TestClient(api.app, base_url="https://testserver", headers={"X-API-Key": api.settings.public_api_key})
    login = client.post(
        "/api/auth/login",
        json={"username": "reader", "password": "a sufficiently secure password"},
    )
    assert login.status_code == 200
    return client, lot_id


def test_read_only_production_allows_curated_desktop_parity_tools(monkeypatch) -> None:
    client, lot_id = _authenticated_client(monkeypatch)

    map_response = client.get("/api/map/lots")
    assert map_response.status_code == 200
    map_payload = map_response.json()
    assert map_payload["total"] == 1
    assert map_payload["mapped_total"] == 1
    assert map_payload["without_coordinates"] == 0
    assert map_payload["updated_at"]
    assert map_payload["timings"]["server_ms"] >= 0
    assert map_response.headers["etag"]
    cached = client.get(
        "/api/map/lots",
        headers={"If-None-Match": map_response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.headers["x-map-cache"] == "HIT"
    outside = client.get(
        "/api/map/lots",
        params={"west": 30, "south": 50, "east": 31, "north": 51},
    )
    assert outside.status_code == 200
    assert outside.json()["items"] == []
    assert outside.json()["total"] == 1
    map_item = map_payload["items"][0]
    assert map_item["id"] == lot_id
    assert set(map_item) == {
        "id", "title", "address", "current_price", "start_price", "region_code", "status", "is_archived",
        "review_status", "lat", "lon",
    }
    filtered = client.get("/api/map/lots", params={"region_code": "76", "max_start_price": 1_000_000})
    assert [item["id"] for item in filtered.json()["items"]] == [lot_id]
    assert client.get(
        "/api/map/lots", params={"region_code": "77", "max_start_price": 1_000_000}
    ).json()["items"] == []
    detail_response = client.get(f"/api/map/lots/{lot_id}")
    assert detail_response.status_code == 200
    map_detail = detail_response.json()
    assert map_detail["source_name"] == "Тестовая ЭТП"
    assert map_detail["procedure_number"] == "PROC-76-1"
    assert map_detail["image_urls"] == ["https://example.test/photo.jpg"]
    assert map_detail["sources"] == [{
        "processed_lot_id": lot_id,
        "source_system": "test",
        "external_id": "parity-lot",
        "title": "Лот для веб-инструментов",
        "price": 1_000_000.0,
        "url": "https://example.test/lot/parity-lot",
        "is_primary": True,
    }]

    assert client.get("/api/quality").status_code == 200
    assert client.get("/api/sources").status_code == 200
    regions = client.get("/api/regions")
    assert regions.status_code == 200
    assert {"code": "76", "name": "Ярославская область"} in regions.json()
    assert client.get("/api/capabilities").json() == {
        "curated_mode": True,
        "region_sync": False,
        "bulk_torgi_sync": False,
        "background_jobs": False,
    }
    assert client.post(f"/api/lots/{lot_id}/watchlist").json()["watchlisted"] is True
    assert client.get("/api/watchlist").json()["total"] == 1
    assert client.post(f"/api/lots/{lot_id}/notes", json={"content": "Проверить документы"}).status_code == 201
    assert client.get(f"/api/lots/{lot_id}/notes").json()[0]["content"] == "Проверить документы"

    participation = client.put(
        f"/api/lots/{lot_id}/participation",
        json={"etp_accredited": True, "notes": "ЭЦП проверяется"},
    )
    assert participation.status_code == 200
    assert client.get(f"/api/lots/{lot_id}/participation").json()["etp_accredited"] is True

    calculation = client.post(
        f"/api/lots/{lot_id}/max-bid",
        json={
            "scenario_name": "Основной",
            "conservative_sale_price": 2_000_000,
            "repair_cost": 100_000,
            "holding_months": 6,
        },
    )
    assert calculation.status_code == 200
    assert calculation.json()["saved_scenario_id"] is not None
    assert client.get(f"/api/lots/{lot_id}/max-bid-scenarios").json()[0]["name"] == "Основной"

    imported = client.post(
        "/api/search/import",
        json={
            "external_id": "online-2",
            "source": "tbankrot",
            "source_system": "tbankrot.ru",
            "title": "Импортированный результат поиска",
            "description": "",
            "category": "land",
            "region_slug": "76",
            "current_price": 750_000,
            "auction_status": "active",
        },
    )
    assert imported.status_code == 201
    assert client.get("/api/lots").json()["total"] == 2

    # Queue-backed bulk mutations stay outside the curated web surface.
    assert client.post("/api/regions/yaroslavl/sync").status_code == 404
