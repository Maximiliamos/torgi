from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bankrotai.db import CadastreObjectCache, distance_km, utc_now
from bankrotai.geo import CADASTRAL_RE, CadastralObjectResult


CADASTRE_CACHE_TTL = timedelta(days=7)
CADASTRE_STALE_MAX_AGE = timedelta(days=30)
CADASTRE_POINT_CACHE_RADIUS_KM = 0.20


def normalize_cadastre_address(value: str | None) -> str | None:
    normalized = " ".join((value or "").casefold().replace("ё", "е").split())
    return normalized or None


def cached_row_to_result(row: CadastreObjectCache, *, source_prefix: str | None = None) -> CadastralObjectResult:
    source = f"{source_prefix}:{row.source}" if source_prefix else row.source
    return CadastralObjectResult(
        query=row.cadastral_number,
        cadastral_number=row.cadastral_number,
        object_type=row.object_type,
        title=row.title,
        address=row.address,
        lat=row.centroid_lat,
        lon=row.centroid_lon,
        geometry_json=row.geometry_json,
        has_boundary=bool(row.geometry_json),
        source=source,
        confidence="high" if row.is_complete else "medium",
        info=dict(row.attributes_json or {}),
    )


def get_cached_cadastre_object(
    session: Session,
    cadastral_number: str,
    *,
    require_complete: bool = False,
    fresh_only: bool = True,
) -> CadastreObjectCache | None:
    normalized = (cadastral_number or "").replace(" ", "")
    if not CADASTRAL_RE.match(normalized):
        return None
    row = session.get(CadastreObjectCache, normalized)
    if row is None:
        return None
    if require_complete and not row.is_complete:
        return None
    now = utc_now()
    if fresh_only and row.expires_at <= now:
        return None
    if not fresh_only and now - row.fetched_at > CADASTRE_STALE_MAX_AGE:
        return None
    return row


def find_cached_cadastre_candidates(
    session: Session,
    *,
    address: str | None,
    lat: float | None,
    lon: float | None,
    fresh_only: bool = True,
) -> list[CadastreObjectCache]:
    now = utc_now()
    normalized_address = normalize_cadastre_address(address)
    conditions = []
    if normalized_address:
        conditions.append(CadastreObjectCache.address_normalized == normalized_address)
    if lat is not None and lon is not None:
        # Cheap indexed prefilter; the exact Haversine check follows below.
        conditions.append(
            (
                CadastreObjectCache.centroid_lat.between(float(lat) - 0.003, float(lat) + 0.003)
                & CadastreObjectCache.centroid_lon.between(float(lon) - 0.004, float(lon) + 0.004)
            )
        )
    if not conditions:
        return []

    stmt = select(CadastreObjectCache).where(or_(*conditions))
    if fresh_only:
        stmt = stmt.where(CadastreObjectCache.expires_at > now)
    else:
        stmt = stmt.where(CadastreObjectCache.fetched_at > now - CADASTRE_STALE_MAX_AGE)
    rows = list(session.scalars(stmt).all())

    result: list[CadastreObjectCache] = []
    seen: set[str] = set()
    for row in rows:
        if row.cadastral_number in seen:
            continue
        if (
            lat is not None
            and lon is not None
            and row.centroid_lat is not None
            and row.centroid_lon is not None
            and distance_km(float(lat), float(lon), row.centroid_lat, row.centroid_lon)
            > CADASTRE_POINT_CACHE_RADIUS_KM
            and row.address_normalized != normalized_address
        ):
            continue
        seen.add(row.cadastral_number)
        result.append(row)

    result.sort(key=lambda item: (not item.is_complete, item.object_type or "", item.cadastral_number))
    return result


def merge_cadastre_result(
    result: CadastralObjectResult,
    cached: CadastreObjectCache | None,
) -> CadastralObjectResult:
    if cached is None:
        return result

    if not result.address and cached.address:
        result.address = cached.address
    if not result.object_type and cached.object_type:
        result.object_type = cached.object_type
    if not result.title and cached.title:
        result.title = cached.title
    if result.lat is None and cached.centroid_lat is not None:
        result.lat = cached.centroid_lat
    if result.lon is None and cached.centroid_lon is not None:
        result.lon = cached.centroid_lon
    if not result.geometry_json and cached.geometry_json:
        result.geometry_json = cached.geometry_json
        result.has_boundary = True

    merged_info = dict(cached.attributes_json or {})
    merged_info.update({key: value for key, value in (result.info or {}).items() if value not in (None, "")})
    if result.address and not merged_info.get("Адрес"):
        merged_info["Адрес"] = result.address
    result.info = merged_info
    return result


def upsert_cadastre_cache(
    session: Session,
    result: CadastralObjectResult,
    *,
    complete: bool,
    address_hint: str | None = None,
) -> CadastreObjectCache | None:
    number = (result.cadastral_number or "").replace(" ", "")
    if not CADASTRAL_RE.match(number):
        return None

    now = utc_now()
    address = result.address or address_hint
    existing = session.get(CadastreObjectCache, number)

    if existing is not None and existing.is_complete and not complete:
        if not existing.address and address:
            existing.address = address
            existing.address_normalized = normalize_cadastre_address(address)
        if existing.geometry_json is None and result.geometry_json:
            existing.geometry_json = result.geometry_json
        if existing.centroid_lat is None and result.lat is not None:
            existing.centroid_lat = result.lat
        if existing.centroid_lon is None and result.lon is not None:
            existing.centroid_lon = result.lon
        existing.updated_at = now
        session.flush()
        return existing

    values = {
        "address": address,
        "address_normalized": normalize_cadastre_address(address),
        "object_type": result.object_type,
        "title": result.title,
        "attributes_json": dict(result.info or {}),
        "geometry_json": result.geometry_json,
        "centroid_lat": result.lat,
        "centroid_lon": result.lon,
        "source": result.source or "unknown",
        "is_complete": bool(complete),
        "fetched_at": now,
        "expires_at": now + CADASTRE_CACHE_TTL,
        "updated_at": now,
    }

    if existing is None:
        existing = CadastreObjectCache(
            cadastral_number=number,
            created_at=now,
            **values,
        )
        session.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)

    session.flush()
    return existing


def cache_results(
    session: Session,
    results: Iterable[CadastralObjectResult],
    *,
    complete: bool,
    address_hint: str | None = None,
) -> None:
    for result in results:
        upsert_cadastre_cache(
            session,
            result,
            complete=complete,
            address_hint=address_hint,
        )
