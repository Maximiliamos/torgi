from __future__ import annotations

from datetime import datetime, timezone

from bankrotai.core import AppSettings, load_settings
from bankrotai.services.map_object_store import (
    _signed_headers,
    dataset_public_tile_base_url,
    object_store_configured,
    object_store_public_enabled,
)
from bankrotai.services.map_payload import public_yandex_tile_payload


def test_public_map_payload_strips_internal_review_state():
    payload = {
        "yandex": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": 7,
                    "geometry": {"type": "Point", "coordinates": [57.6, 39.8]},
                    "properties": {
                        "kind": "lot",
                        "lotId": 7,
                        "title": "Lot",
                        "status": "active",
                        "review_status": "approved",
                    },
                    "options": {"preset": "islands#greenDotIcon"},
                },
                {
                    "type": "Feature",
                    "id": 8,
                    "geometry": {"type": "Point", "coordinates": [57.7, 39.9]},
                    "properties": {
                        "kind": "lot",
                        "lotId": 8,
                        "title": "Ended",
                        "status": "completed",
                        "review_status": "rejected",
                    },
                    "options": {"preset": "islands#redDotIcon"},
                },
            ],
        }
    }

    public = public_yandex_tile_payload(payload)

    assert public["type"] == "FeatureCollection"
    assert public["features"][0]["properties"].get("review_status") is None
    assert public["features"][0]["options"]["preset"] == "islands#grayDotIcon"
    assert public["features"][1]["properties"].get("review_status") is None
    assert public["features"][1]["options"]["preset"] == "islands#blackDotIcon"


def test_public_tile_url_does_not_require_write_credentials():
    settings = AppSettings(
        map_object_store_enabled=True,
        map_object_store_public_base_url="https://map-storage.example/public/",
    )

    assert object_store_public_enabled(settings) is True
    assert object_store_configured(settings) is False
    assert dataset_public_tile_base_url("dataset:v1", settings) == (
        "https://map-storage.example/public/datasets/dataset%3Av1/tiles"
    )


def test_s3_signature_uses_configured_endpoint_bucket_and_region():
    url, headers = _signed_headers(
        method="PUT",
        endpoint="https://s3.regru.cloud",
        bucket="sterdez-map",
        key="datasets/v1/tiles/7/77/38.json",
        body=b"{}",
        content_type="application/json; charset=utf-8",
        cache_control="public, max-age=31536000, immutable",
        access_key="ACCESS",
        secret_key="SECRET",
        region="ru-1",
        now=datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc),
    )

    assert url == "https://s3.regru.cloud/sterdez-map/datasets/v1/tiles/7/77/38.json"
    assert headers["Host"] == "s3.regru.cloud"
    assert headers["X-Amz-Date"] == "20260924T180000Z"
    assert headers["X-Amz-Content-Sha256"]
    assert "Credential=ACCESS/20260924/ru-1/s3/aws4_request" in headers["Authorization"]
    assert "SignedHeaders=cache-control;content-type;host;x-amz-content-sha256;x-amz-date" in headers["Authorization"]


def test_regru_settings_replace_unusable_website_public_url(monkeypatch):
    monkeypatch.setenv("MAP_OBJECT_STORE_ENDPOINT", "https://s3.regru.cloud")
    monkeypatch.setenv("MAP_OBJECT_STORE_BUCKET", "sterdez-map")
    monkeypatch.setenv(
        "MAP_OBJECT_STORE_PUBLIC_BASE_URL",
        "https://sterdez-map.website.regru.cloud",
    )
    monkeypatch.setenv("MAP_OBJECT_STORE_REGION", "ru-1")

    settings = load_settings()

    assert settings.map_object_store_public_base_url == "https://s3.regru.cloud/sterdez-map"
    assert settings.map_object_store_region == "ru-1"


def test_regru_settings_preserve_explicit_custom_public_domain(monkeypatch):
    monkeypatch.setenv("MAP_OBJECT_STORE_ENDPOINT", "https://s3.regru.cloud")
    monkeypatch.setenv("MAP_OBJECT_STORE_BUCKET", "sterdez-map")
    monkeypatch.setenv("MAP_OBJECT_STORE_PUBLIC_BASE_URL", "https://maps.example.test")

    settings = load_settings()

    assert settings.map_object_store_public_base_url == "https://maps.example.test"
