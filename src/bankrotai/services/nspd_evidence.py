"""Normalize a time-bounded, SHA-checked primary NSPD response without HTTP."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

from bankrotai.geo import CadastralObjectResult
from bankrotai.services.cadastral_identity import pick_nspd_feature
from bankrotai.services.geo_cadastral_properties import normalize_nspd_props
from bankrotai.services.geo_geometry import centroid_from_geometry, geometry_to_wgs84


def nspd_result_from_receipt(receipt: dict, *, now: datetime | None = None) -> CadastralObjectResult:
    if receipt.get("provider") != "nspd" or receipt.get("http_status") != 200:
        raise ValueError("Expected a successful primary NSPD receipt")
    checked = datetime.fromisoformat(receipt["checked_at_utc"])
    current = now or datetime.now(timezone.utc)
    if checked.tzinfo is None or not timedelta(0) <= current - checked <= timedelta(hours=24):
        raise ValueError("NSPD evidence is expired or has an invalid timestamp")
    body = base64.b64decode(receipt["response_base64"], validate=True)
    if hashlib.sha256(body).hexdigest() != receipt["response_sha256"]:
        raise ValueError("NSPD response content changed")
    payload = json.loads(body)
    features = (payload.get("data") or {}).get("features") or payload.get("features") or []
    cadastre = receipt["cadastral_number"]
    feature = pick_nspd_feature(features, cadastre)
    if feature is None:
        raise ValueError("Primary NSPD response has no exact cadastral identity")
    info = normalize_nspd_props(feature.get("properties") or {}, "")
    geometry = geometry_to_wgs84(feature.get("geometry"))
    lat, lon = centroid_from_geometry(geometry)
    if lat is None or lon is None:
        raise ValueError("Primary NSPD feature has no usable coordinates")
    boundary = geometry if geometry and geometry.get("type") != "Point" else None
    return CadastralObjectResult(
        query=cadastre, cadastral_number=cadastre,
        object_type=info.get("Вид объекта недвижимости"),
        title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
        address=info.get("Адрес"), lat=lat, lon=lon,
        geometry_json=boundary, has_boundary=bool(boundary),
        source="nspd", confidence="high", info=info, raw=feature,
    )
