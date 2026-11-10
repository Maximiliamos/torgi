"""Read-only GEO provider diagnostics from the running app container.

Exactly one Photon /api request and one NSPD cadastral request are allowed.
Does not call the geocoding resolver, record provider health, modify Redis,
write database records, trigger a job, or publish a map.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time
from urllib.parse import urlparse

import requests

from bankrotai.geo import nspd_tls_verify
from bankrotai.services.geo_resilience import classify_transport_exception


_NSPD_ENDPOINT = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
_CONTROL_CADASTRE = "47:22:0325001:373"
_CONTROL_ADDRESS = "Москва, Ленинградское шоссе, дом 228"


def probe_photon() -> dict:
    url = os.getenv("PHOTON_BASE_URL", "").strip().rstrip("/")
    if not url:
        return {"state": "not_configured", "url_configured": False}
    parts = urlparse(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return {"state": "invalid_configuration", "url_configured": True}
    started = time.monotonic()
    try:
        response = requests.get(
            url + "/api",
            params={"q": _CONTROL_ADDRESS, "limit": 5, "lang": "ru", "countrycode": "RU"},
            timeout=(2, 6),
        )
        response.raise_for_status()
        data = response.json()
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            return {"state": "invalid_response", "url_configured": True}
        return {
            "state": "reachable" if features else "reachable_no_results",
            "url_configured": True,
            "feature_count": len(features),
            "latency_ms": round((time.monotonic() - started) * 1000),
            "http_status": response.status_code,
            "note": "Feature count alone does not prove the correct house was found",
        }
    except (requests.RequestException, ValueError, AttributeError) as exc:
        return {
            "state": "request_failed",
            "url_configured": True,
            "reason": classify_transport_exception(exc),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }


def probe_nspd() -> dict:
    started = time.monotonic()
    try:
        response = requests.get(
            _NSPD_ENDPOINT,
            params={"thematicSearchId": 1, "query": _CONTROL_CADASTRE},
            headers={
                "Referer": "https://nspd.gov.ru/map?thematic=PKK",
                "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=(2, 5),
            verify=nspd_tls_verify(),  # use the same verified CA policy as the app
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"state": "invalid_response"}
        features = (data.get("data") or {}).get("features") or data.get("features") or []
        if not isinstance(features, list):
            return {"state": "invalid_response"}
        exact = 0
        with_geometry = 0
        for feature in features[:100]:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            candidates = [
                props.get(key) for key in (
                    "cad_num", "cadNum", "cadastralNumber", "cadastral_number",
                    "cn", "label",
                )
            ]
            exact += int(any(str(v or "").replace(" ", "") == _CONTROL_CADASTRE for v in candidates))
            with_geometry += int(bool(feature.get("geometry")))
        return {
            "state": "reachable" if features else "reachable_no_results",
            "feature_count": len(features),
            "exact_cadastral_labels": exact,
            "features_with_any_geometry": with_geometry,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "http_status": response.status_code,
            "note": "Geometry presence is not proof of valid coordinates or identity",
        }
    except (requests.RequestException, ValueError, AttributeError) as exc:
        return {
            "state": "request_failed",
            "reason": classify_transport_exception(exc),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }


def run() -> dict:
    return {
        "report": "geo_p0_provider_probe",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_writes": 0,
        "redis_writes": 0,
        "request_count_max": 2,
        "checks": {"photon": probe_photon(), "nspd": probe_nspd()},
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, separators=(",", ":")))
