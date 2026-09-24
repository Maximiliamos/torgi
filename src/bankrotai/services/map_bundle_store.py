from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.core import AppSettings, get_settings
from bankrotai.db import MapDataset, MapTile
from bankrotai.services.map_object_store import (
    _put_object,
    _verify_public_manifest,
    object_store_configured,
)
from bankrotai.services.map_payload import legacy_tile_to_yandex, public_yandex_tile_payload


logger = logging.getLogger(__name__)

REGIONAL_BUNDLE_LAYOUT = "regional-bundles-v1"
POINT_ZOOM = 12
DETAIL_PARENT_ZOOM = 8
OVERVIEW_PARENT_ZOOM = 6
_UPLOAD_PAGE_SIZE = 500

# Central Federal District. The map can still serve every region; this list is
# only a client hint for preload/prefetch priority.
CFO_REGION_CODES = frozenset(
    {
        "31",  # Belgorod
        "32",  # Bryansk
        "33",  # Vladimir
        "36",  # Voronezh
        "37",  # Ivanovo
        "40",  # Kaluga
        "44",  # Kostroma
        "46",  # Kursk
        "48",  # Lipetsk
        "50",  # Moscow Oblast
        "57",  # Oryol
        "62",  # Ryazan
        "67",  # Smolensk
        "68",  # Tambov
        "69",  # Tver
        "71",  # Tula
        "76",  # Yaroslavl
        "77",  # Moscow
    }
)


def normalize_map_region_code(
    region_code: str | int | None,
    cadastral_number: str | None = None,
) -> str | None:
    """Return a stable cadastral-region code.

    A cadastral number is preferred when available because source-system region
    identifiers are not always cadastral/FIAS codes (for example historical
    aliases can exist in upstream auction feeds).
    """

    cadastral = str(cadastral_number or "").strip()
    if ":" in cadastral:
        prefix = cadastral.split(":", 1)[0].strip()
        if prefix.isdigit() and 1 <= len(prefix) <= 2:
            return prefix.zfill(2)

    raw = str(region_code or "").strip()
    if raw.isdigit() and 1 <= len(raw) <= 2:
        return raw.zfill(2)
    if raw.isdigit() and len(raw) == 3 and raw.startswith("0"):
        return raw[-2:]
    return raw or None


def _tile_region_code(z: int, payload: dict[str, Any]) -> str:
    if z < POINT_ZOOM:
        return "_overview"

    region_codes: set[str] = set()
    for feature in payload.get("features", []):
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict) or properties.get("kind") != "lot":
            continue
        normalized = normalize_map_region_code(
            properties.get("bundle_region_code") or properties.get("region_code")
        )
        if normalized:
            region_codes.add(normalized)

    if len(region_codes) == 1:
        return next(iter(region_codes))
    if len(region_codes) > 1:
        return "_multi"
    return "_unknown"


def _bundle_bucket(z: int, x: int, y: int, region_code: str) -> str:
    """Use stable spatial boundaries so one changed tile only changes one bundle."""

    if z < 7:
        return f"{region_code}/z{z}"
    parent_zoom = OVERVIEW_PARENT_ZOOM if z < POINT_ZOOM else DETAIL_PARENT_ZOOM
    shift = max(0, z - parent_zoom)
    parent_x = x >> shift
    parent_y = y >> shift
    return f"{region_code}/p{parent_zoom}/{parent_x}/{parent_y}"


def _bundle_object_key(body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"bundles/v1/{digest[:2]}/{digest}.json"


def _index_object_key(body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"indexes/v1/{digest[:2]}/{digest}.json"


def _index_shard(z: int, x: int, y: int) -> str:
    """Return the small spatial routing shard needed to resolve one logical tile."""

    if z <= OVERVIEW_PARENT_ZOOM:
        return "overview/root"
    if z < POINT_ZOOM:
        shift = z - OVERVIEW_PARENT_ZOOM
        return f"overview/{OVERVIEW_PARENT_ZOOM}/{x >> shift}/{y >> shift}"
    shift = z - DETAIL_PARENT_ZOOM
    return f"detail/{DETAIL_PARENT_ZOOM}/{x >> shift}/{y >> shift}"


def _public_object_url(settings: AppSettings, key: str) -> str:
    assert settings.map_object_store_public_base_url is not None
    return f"{settings.map_object_store_public_base_url.rstrip('/')}/{key.lstrip('/')}"


def _public_object_exists(settings: AppSettings, key: str) -> bool:
    """Best-effort content-addressed reuse check.

    False is safe: a repeated PUT to the same hash key is idempotent.
    """

    try:
        response = requests.head(
            _public_object_url(settings, key),
            headers={"Cache-Control": "no-cache"},
            timeout=min(settings.map_object_store_timeout_seconds, 20.0),
        )
    except requests.RequestException:
        return False
    return response.status_code == 200


def _write_json_object(
    settings: AppSettings,
    key: str,
    value: dict[str, Any],
    *,
    cache_control: str,
) -> int:
    body = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _put_object(settings, key, body, cache_control=cache_control)
    return len(body)


def _tile_bounds_from_payload(payload: dict[str, Any]) -> list[float] | None:
    lats: list[float] = []
    lons: list[float] = []
    for feature in payload.get("features", []):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            continue
        try:
            lat = float(coordinates[0])
            lon = float(coordinates[1])
        except (TypeError, ValueError):
            continue
        lats.append(lat)
        lons.append(lon)
    if not lats:
        return None
    return [min(lons), min(lats), max(lons), max(lats)]


def publish_dataset_to_regional_bundles(
    session_factory: Callable[[], Session],
    *,
    dataset_id: int,
    version: str,
) -> dict[str, Any]:
    """Publish immutable tile bundles grouped by region and stable spatial shard.

    The browser still consumes ordinary logical z/x/y tiles. Physical S3
    objects are content-addressed bundles, so unchanged bundles are reusable
    across dataset versions and a failed publication can resume naturally.
    """

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
            raise RuntimeError(f"Map dataset {dataset_id} disappeared before bundle publication")
        expected_tile_count = int(dataset.tile_count)
        point_count = int(dataset.point_count)

    groups: dict[str, dict[str, Any]] = defaultdict(dict)
    index_entries: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    group_regions: dict[str, str] = {}
    group_feature_counts: dict[str, int] = defaultdict(int)
    region_point_counts: dict[str, int] = defaultdict(int)
    region_bounds: dict[str, list[float]] = {}
    seen_tiles = 0
    last_id = 0

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

        for row in rows:
            prepared_payload = legacy_tile_to_yandex(row.payload_json)
            region_code = _tile_region_code(int(row.z), prepared_payload)
            public_payload = public_yandex_tile_payload(row.payload_json)
            bucket = _bundle_bucket(int(row.z), int(row.x), int(row.y), region_code)
            tile_key = f"{int(row.z)}/{int(row.x)}/{int(row.y)}"
            groups[bucket][tile_key] = public_payload
            group_regions[bucket] = region_code
            group_feature_counts[bucket] += len(public_payload.get("features", []))
            index_entries[_index_shard(int(row.z), int(row.x), int(row.y))][tile_key] = {
                "group": bucket,
                "region": region_code,
            }
            seen_tiles += 1

            # Count each lot once at POINT_ZOOM and collect a coarse region bbox.
            if int(row.z) == POINT_ZOOM and region_code not in {"_multi", "_unknown", "_overview"}:
                features = public_payload.get("features", [])
                region_point_counts[region_code] += sum(
                    1
                    for feature in features
                    if isinstance(feature, dict)
                    and isinstance(feature.get("properties"), dict)
                    and feature["properties"].get("kind") == "lot"
                )
                bounds = _tile_bounds_from_payload(public_payload)
                if bounds is not None:
                    existing = region_bounds.get(region_code)
                    if existing is None:
                        region_bounds[region_code] = bounds
                    else:
                        region_bounds[region_code] = [
                            min(existing[0], bounds[0]),
                            min(existing[1], bounds[1]),
                            max(existing[2], bounds[2]),
                            max(existing[3], bounds[3]),
                        ]

    if seen_tiles != expected_tile_count:
        raise RuntimeError(
            f"Regional bundle source incomplete: expected={expected_tile_count} tiles read={seen_tiles}"
        )

    immutable_cache = "public, max-age=31536000, immutable"
    bundle_specs: dict[str, tuple[str, bytes, str]] = {}
    group_to_object: dict[str, str] = {}

    for group_key in sorted(groups):
        region_code = group_regions[group_key]
        value = {
            "layout": REGIONAL_BUNDLE_LAYOUT,
            "region": region_code,
            "bucket": group_key,
            "tiles": groups[group_key],
        }
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        object_key = _bundle_object_key(body)
        bundle_specs[group_key] = (object_key, body, region_code)
        group_to_object[group_key] = object_key

    index_specs: dict[str, tuple[str, bytes]] = {}
    shard_to_object: dict[str, str] = {}
    for shard_key in sorted(index_entries):
        entries = index_entries[shard_key]
        resolved_tiles = {
            coordinate: {
                "bundle": group_to_object[value["group"]],
                "region": value["region"],
            }
            for coordinate, value in sorted(entries.items())
        }
        value = {
            "layout": REGIONAL_BUNDLE_LAYOUT,
            "shard": shard_key,
            "tiles": resolved_tiles,
        }
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        object_key = _index_object_key(body)
        index_specs[shard_key] = (object_key, body)
        shard_to_object[shard_key] = object_key

    uploaded_bundles = 0
    reused_bundles = 0
    uploaded_indexes = 0
    reused_indexes = 0
    uploaded_bytes = 0
    reused_bytes = 0

    def ensure_immutable(key: str, body: bytes) -> tuple[bool, int]:
        if _public_object_exists(settings, key):
            return True, len(body)
        _put_object(settings, key, body, cache_control=immutable_cache)
        return False, len(body)

    work: list[tuple[str, str, bytes]] = [
        ("bundle", key, body)
        for key, body, _region in bundle_specs.values()
    ] + [
        ("index", key, body)
        for key, body in index_specs.values()
    ]

    with ThreadPoolExecutor(max_workers=settings.map_object_store_workers) as pool:
        futures = {
            pool.submit(ensure_immutable, key, body): (kind, key)
            for kind, key, body in work
        }
        completed = 0
        for future in as_completed(futures):
            kind, key = futures[future]
            try:
                reused, size = future.result()
            except Exception:
                logger.exception(
                    "REG.RU S3 immutable map object upload failed: version=%s kind=%s key=%s",
                    version,
                    kind,
                    key,
                )
                raise
            completed += 1
            if reused:
                reused_bytes += size
                if kind == "bundle":
                    reused_bundles += 1
                else:
                    reused_indexes += 1
            else:
                uploaded_bytes += size
                if kind == "bundle":
                    uploaded_bundles += 1
                else:
                    uploaded_indexes += 1
            if completed % 25 == 0 or completed == len(work):
                logger.info(
                    "REG.RU S3 bundle publication progress: version=%s completed=%s/%s "
                    "bundles_uploaded=%s bundles_reused=%s indexes_uploaded=%s indexes_reused=%s "
                    "uploaded_bytes=%s reused_bytes=%s",
                    version,
                    completed,
                    len(work),
                    uploaded_bundles,
                    reused_bundles,
                    uploaded_indexes,
                    reused_indexes,
                    uploaded_bytes,
                    reused_bytes,
                )

    dataset_root = f"datasets/{version}"

    regions = {
        region_code: {
            "point_count": region_point_counts.get(region_code, 0),
            "bounds": region_bounds.get(region_code),
            "priority": region_code in CFO_REGION_CODES,
            "bundle_count": sum(
                1
                for _group, (_key, _body, group_region) in bundle_specs.items()
                if group_region == region_code
            ),
        }
        for region_code in sorted(
            set(region_point_counts)
            | {region for region in group_regions.values() if not region.startswith("_")}
        )
    }

    manifest = {
        "version": version,
        "layout": REGIONAL_BUNDLE_LAYOUT,
        "point_count": point_count,
        "tile_count": expected_tile_count,
        "bundle_count": len(bundle_specs),
        "index_shard_count": len(index_specs),
        "uploaded_bundle_count": uploaded_bundles,
        "reused_bundle_count": reused_bundles,
        "uploaded_index_count": uploaded_indexes,
        "reused_index_count": reused_indexes,
        "uploaded_bytes": uploaded_bytes,
        "reused_bytes": reused_bytes,
        "point_zoom": POINT_ZOOM,
        "detail_parent_zoom": DETAIL_PARENT_ZOOM,
        "overview_parent_zoom": OVERVIEW_PARENT_ZOOM,
        "priority_regions": sorted(CFO_REGION_CODES),
        "regions": regions,
        "index_shards": shard_to_object,
        "bundle_root_url": settings.map_object_store_public_base_url,
    }
    manifest_bytes = _write_json_object(
        settings,
        f"{dataset_root}/manifest.json",
        manifest,
        cache_control="public, max-age=60",
    )

    verified = _verify_public_manifest(settings, version)
    if verified.get("layout") != REGIONAL_BUNDLE_LAYOUT:
        raise RuntimeError(
            "REG.RU S3 regional bundle manifest layout verification failed: "
            f"actual={verified.get('layout')!r}"
        )

    return {
        "status": "published",
        "layout": REGIONAL_BUNDLE_LAYOUT,
        "bundle_count": len(bundle_specs),
        "uploaded_bundle_count": uploaded_bundles,
        "reused_bundle_count": reused_bundles,
        "uploaded_index_count": uploaded_indexes,
        "reused_index_count": reused_indexes,
        "uploaded_bytes": uploaded_bytes,
        "reused_bytes": reused_bytes,
        "index_shard_count": len(index_specs),
        "manifest_bytes": manifest_bytes,
        "regions": regions,
    }
