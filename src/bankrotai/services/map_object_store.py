from __future__ import annotations

import hashlib
import hmac
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.core import Settings, get_settings
from bankrotai.db import MapDataset, MapTile
from bankrotai.services.map_payload import public_yandex_tile_payload

logger = logging.getLogger(__name__)
_UPLOAD_PAGE_SIZE = 250
_SERVICE = "s3"


def object_store_public_enabled(settings: Settings | None = None) -> bool:
    value = settings or get_settings()
    return bool(value.map_object_store_enabled and value.map_object_store_public_base_url)


def object_store_configured(settings: Settings | None = None) -> bool:
    value = settings or get_settings()
    return bool(
        object_store_public_enabled(value)
        and value.map_object_store_endpoint
        and value.map_object_store_bucket
        and value.map_object_store_access_key
        and value.map_object_store_secret_key
    )


def dataset_public_tile_base_url(version: str, settings: Settings | None = None) -> str | None:
    value = settings or get_settings()
    if not object_store_public_enabled(value):
        return None
    root = str(value.map_object_store_public_base_url).rstrip("/")
    return f"{root}/datasets/{quote(version, safe='-_.~')}/tiles"


def _canonical_object_url(endpoint: str, bucket: str, key: str) -> tuple[str, str, str]:
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MAP_OBJECT_STORE_ENDPOINT must be an absolute HTTP(S) URL")
    bucket_part = quote(bucket, safe="-_.~")
    key_part = quote(key.lstrip("/"), safe="/-_.~")
    base_path = parsed.path.rstrip("/")
    canonical_uri = f"{base_path}/{bucket_part}/{key_part}" or "/"
    return (
        urlunsplit((parsed.scheme, parsed.netloc, canonical_uri, "", "")),
        parsed.netloc,
        canonical_uri,
    )


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    date_key = hmac.new(("AWS4" + secret).encode(), date_stamp.encode(), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode(), hashlib.sha256).digest()
    service_key = hmac.new(region_key, _SERVICE.encode(), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _signed_headers(
    *,
    method: str,
    endpoint: str,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    cache_control: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime | None = None,
) -> tuple[str, dict[str, str]]:
    current = now or datetime.now(timezone.utc)
    amz_date = current.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = current.strftime("%Y%m%d")
    url, host, canonical_uri = _canonical_object_url(endpoint, bucket, key)
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "cache-control": cache_control,
        "content-type": content_type,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_header_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in sorted(headers))
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            "",
            canonical_headers,
            signed_header_names,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    headers["authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, "
        f"Signature={signature}"
    )
    return url, {name.title(): value for name, value in headers.items()}


def _put_object(settings: Settings, key: str, body: bytes, *, cache_control: str) -> None:
    assert settings.map_object_store_access_key is not None
    assert settings.map_object_store_secret_key is not None
    url, headers = _signed_headers(
        method="PUT",
        endpoint=settings.map_object_store_endpoint,
        bucket=settings.map_object_store_bucket,
        key=key,
        body=body,
        content_type="application/json; charset=utf-8",
        cache_control=cache_control,
        access_key=settings.map_object_store_access_key,
        secret_key=settings.map_object_store_secret_key,
        region=settings.map_object_store_region,
    )
    response = requests.put(
        url,
        data=body,
        headers=headers,
        timeout=settings.map_object_store_timeout_seconds,
    )
    if response.status_code not in {200, 201, 204}:
        detail = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"REG.RU S3 PUT failed for {key}: HTTP {response.status_code} {detail}")


def _verify_public_manifest(settings: Settings, version: str) -> dict[str, Any]:
    assert settings.map_object_store_public_base_url is not None
    url = (
        f"{settings.map_object_store_public_base_url.rstrip('/')}"
        f"/datasets/{quote(version, safe='-_.~')}/manifest.json"
    )
    origin = "https://sterdez.online"
    response = requests.get(
        url,
        headers={"Origin": origin, "Cache-Control": "no-cache"},
        timeout=settings.map_object_store_timeout_seconds,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"REG.RU S3 public manifest verification failed: HTTP {response.status_code} at {url}"
        )
    allow_origin = response.headers.get("access-control-allow-origin")
    if allow_origin not in {"*", origin}:
        raise RuntimeError(
            "REG.RU S3 bucket must allow browser CORS GET from https://sterdez.online "
            f"(received Access-Control-Allow-Origin={allow_origin!r})"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("REG.RU S3 public manifest is not valid JSON") from exc
    if payload.get("version") != version:
        raise RuntimeError(
            f"REG.RU S3 public manifest version mismatch: expected={version} actual={payload.get('version')}"
        )
    return payload


def publish_dataset_to_object_store(
    session_factory: Callable[[], Session],
    *,
    dataset_id: int,
    version: str,
) -> dict[str, Any]:
    """Upload every immutable public map tile before the dataset can be promoted."""
    settings = get_settings()
    if not settings.map_object_store_enabled:
        return {"status": "disabled"}
    if not object_store_configured(settings):
        raise RuntimeError(
            "MAP_OBJECT_STORE_ENABLED=true requires endpoint, bucket, public base URL, access key and secret key"
        )

    with session_factory() as session:
        dataset = session.get(MapDataset, dataset_id)
        if dataset is None:
            raise RuntimeError(f"Map dataset {dataset_id} disappeared before object-store publication")
        expected_tile_count = int(dataset.tile_count)
        point_count = int(dataset.point_count)

    uploaded = 0
    uploaded_bytes = 0
    last_id = 0
    immutable_cache = "public, max-age=31536000, immutable"

    while True:
        with session_factory() as session:
            rows = session.execute(
                select(
                    MapTile.id,
                    MapTile.z,
                    MapTile.x,
                    MapTile.y,
                    MapTile.payload_json,
                )
                .where(MapTile.dataset_id == dataset_id, MapTile.id > last_id)
                .order_by(MapTile.id)
                .limit(_UPLOAD_PAGE_SIZE)
            ).all()
        if not rows:
            break
        last_id = int(rows[-1].id)

        objects: list[tuple[str, bytes]] = []
        for row in rows:
            payload = public_yandex_tile_payload(row.payload_json)
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            key = f"datasets/{version}/tiles/{row.z}/{row.x}/{row.y}.json"
            objects.append((key, body))

        with ThreadPoolExecutor(max_workers=settings.map_object_store_workers) as pool:
            futures = {
                pool.submit(_put_object, settings, key, body, cache_control=immutable_cache): (key, len(body))
                for key, body in objects
            }
            for future in as_completed(futures):
                key, size = futures[future]
                try:
                    future.result()
                except Exception:
                    logger.exception("REG.RU S3 tile upload failed: version=%s key=%s", version, key)
                    raise
                uploaded += 1
                uploaded_bytes += size

    if uploaded != expected_tile_count:
        raise RuntimeError(
            f"REG.RU S3 tile publication incomplete: expected={expected_tile_count} uploaded={uploaded}"
        )

    manifest = {
        "version": version,
        "point_count": point_count,
        "tile_count": uploaded,
        "format": "yandex-feature-collection-v1",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_body = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _put_object(
        settings,
        f"datasets/{version}/manifest.json",
        manifest_body,
        cache_control="public, max-age=60",
    )
    verified = _verify_public_manifest(settings, version)
    logger.info(
        "REG.RU S3 map publication verified: version=%s tiles=%s bytes=%s",
        version,
        uploaded,
        uploaded_bytes,
    )
    return {
        "status": "published",
        "tile_count": uploaded,
        "bytes": uploaded_bytes,
        "public_tile_base_url": dataset_public_tile_base_url(version, settings),
        "manifest": verified,
    }
