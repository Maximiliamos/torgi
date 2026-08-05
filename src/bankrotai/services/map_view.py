from __future__ import annotations

from datetime import datetime
from time import perf_counter
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bankrotai.core import get_region_query_values
from bankrotai.db import LotGeoSnapshot, ProcessedLot, SourceLot


def extract_map_image_urls(raw_data: object) -> list[str]:
    """Extract unique public image URLs from heterogeneous source payloads."""
    preferred_keys = (
        "image_url", "photo_url", "thumbnail_url", "main_image", "image",
        "photo", "thumbnail", "image_urls", "photo_urls", "images", "photos", "gallery",
    )
    found: list[str] = []

    def collect(value: object, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            parsed = urlparse(candidate)
            if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                found.append(candidate)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item, depth + 1)
            return
        if isinstance(value, dict):
            lowered = {str(key).lower(): item for key, item in value.items()}
            if depth > 0:
                for key in ("url", "src"):
                    if key in lowered:
                        collect(lowered[key], depth + 1)
            for key in preferred_keys:
                if key in lowered:
                    collect(lowered[key], depth + 1)
            for key, item in lowered.items():
                if any(token in key for token in ("image", "photo", "thumb")):
                    collect(item, depth + 1)

    collect(raw_data)
    return list(dict.fromkeys(found))


def _display_datetime(value: datetime | None) -> str | None:
    return value.strftime("%d.%m.%Y %H:%M") if value else None


def _map_lot_payload(
    lot: ProcessedLot,
    geo: LotGeoSnapshot,
    source_rows: list[tuple[ProcessedLot, SourceLot]],
) -> dict:
    sources = [source for _processed, source in source_rows]
    primary = next(
        (item for item in sources if item.source_system == lot.source_system and item.external_id == lot.external_id),
        sources[0] if sources else None,
    )
    source_url = primary.source_url if primary and primary.source_url else lot.source_url or lot.lot_url
    source_system = (lot.source_system or lot.source or "").casefold()
    images: list[str] = []
    gis_url = None
    etp_url = None
    russia_url = None
    for source in sources:
        raw = source.raw_data if isinstance(source.raw_data, dict) else {}
        for url in extract_map_image_urls(raw):
            if url not in images:
                images.append(url)
        system = (source.source_system or "").casefold()
        gis_url = gis_url or raw.get("gis_torgi_url") or (source.source_url if "torgi.gov" in system else None)
        etp_url = etp_url or raw.get("etp_url") or (source.source_url if "lot-online" in system else None)
        russia_url = russia_url or raw.get("torgi_russia_url")

    auction_at = max((item.auction_at for item in sources if item.auction_at), default=None)
    publications = [
        {
            "processed_lot_id": processed.id,
            "source_system": source.source_system,
            "external_id": source.external_id,
            "title": processed.title,
            "price": float(processed.current_price) if processed.current_price is not None else None,
            "url": source.source_url or processed.source_url or processed.lot_url,
            "is_primary": processed.id == lot.id,
        }
        for processed, source in source_rows
    ]
    if not publications:
        publications.append({
            "processed_lot_id": lot.id,
            "source_system": lot.source_system or lot.source,
            "external_id": lot.external_id,
            "title": lot.title,
            "price": float(lot.current_price) if lot.current_price is not None else None,
            "url": lot.source_url or lot.lot_url,
            "is_primary": True,
        })
    return {
        "id": lot.id,
        "external_id": lot.external_id,
        "title": lot.title,
        "description": lot.description,
        "address": lot.address,
        "cadastral_number": lot.cadastral_number,
        "category": lot.category,
        "region": lot.region_name or lot.region_slug,
        "status": lot.auction_status,
        "is_archived": lot.is_archived,
        "review_status": lot.review_status,
        "current_price": float(lot.current_price) if lot.current_price is not None else None,
        "lat": geo.centroid_lat,
        "lon": geo.centroid_lon,
        "geometry": geo.geometry_json,
        "confidence": geo.geo_confidence,
        "geo_source": geo.geo_source,
        "source": lot.source_system or lot.source,
        "source_name": " / ".join(dict.fromkeys(item.platform_name or item.source_system for item in sources)) if sources else lot.source_system or lot.source,
        "source_url": source_url,
        "gis_torgi_url": gis_url or (source_url if "torgi.gov" in source_system else None),
        "etp_url": etp_url or (source_url if "lot-online" in source_system else None),
        "torgi_russia_url": russia_url,
        "image_url": images[0] if images else None,
        "image_urls": images,
        "procedure_number": next((item.procedure_number for item in sources if item.procedure_number), None),
        "application_deadline": _display_datetime(next((item.application_deadline for item in sources if item.application_deadline), None)),
        "auction_at": _display_datetime(auction_at),
        "sources": publications,
    }


def build_map_lots_response(
    session: Session,
    *,
    city_slug: str | None,
    include_archived: bool,
    limit: int,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    review_status: str | None = None,
) -> dict:
    started = perf_counter()
    filters = [ProcessedLot.duplicate_of_id.is_(None)]
    if city_slug:
        filters.append(ProcessedLot.region_slug.in_(get_region_query_values(city_slug)))
    if not include_archived:
        filters.append(ProcessedLot.is_archived.is_(False))
    if review_status:
        filters.append(ProcessedLot.review_status == review_status)

    latest_geo = (
        select(LotGeoSnapshot.lot_id, func.max(LotGeoSnapshot.id).label("geo_id"))
        .where(LotGeoSnapshot.centroid_lat.isnot(None), LotGeoSnapshot.centroid_lon.isnot(None))
        .group_by(LotGeoSnapshot.lot_id)
        .subquery()
    )
    map_filters = list(filters)
    if all(value is not None for value in (west, south, east, north)):
        assert west is not None and south is not None and east is not None and north is not None
        map_filters.extend((
            LotGeoSnapshot.centroid_lat >= south,
            LotGeoSnapshot.centroid_lat <= north,
        ))
        if west <= east:
            map_filters.extend((
                LotGeoSnapshot.centroid_lon >= west,
                LotGeoSnapshot.centroid_lon <= east,
            ))
        else:
            map_filters.append(or_(
                LotGeoSnapshot.centroid_lon >= west,
                LotGeoSnapshot.centroid_lon <= east,
            ))

    statement = (
        select(
            ProcessedLot.id,
            ProcessedLot.title,
            ProcessedLot.address,
            ProcessedLot.current_price,
            ProcessedLot.auction_status,
            ProcessedLot.is_archived,
            ProcessedLot.review_status,
            LotGeoSnapshot.centroid_lat,
            LotGeoSnapshot.centroid_lon,
        )
        .join(latest_geo, latest_geo.c.lot_id == ProcessedLot.id)
        .join(LotGeoSnapshot, LotGeoSnapshot.id == latest_geo.c.geo_id)
        .where(*map_filters)
        .order_by(ProcessedLot.last_update.desc())
        .limit(limit)
    )
    rows = session.execute(statement).all()

    total = session.scalar(select(func.count(ProcessedLot.id)).where(*filters)) or 0
    mapped_total = session.scalar(
        select(func.count(ProcessedLot.id))
        .join(latest_geo, latest_geo.c.lot_id == ProcessedLot.id)
        .where(*filters)
    ) or 0
    updated_at = session.scalar(select(func.max(ProcessedLot.last_update)).where(*filters))

    items = [
        {
            "id": row.id,
            "title": row.title,
            "address": row.address,
            "current_price": float(row.current_price) if row.current_price is not None else None,
            "status": row.auction_status,
            "is_archived": row.is_archived,
            "review_status": row.review_status,
            "lat": row.centroid_lat,
            "lon": row.centroid_lon,
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "mapped_total": mapped_total,
        "without_coordinates": max(total - mapped_total, 0),
        "updated_at": updated_at,
        "timings": {"server_ms": round((perf_counter() - started) * 1000, 1)},
    }


def build_map_lot_detail(session: Session, lot_id: int) -> dict | None:
    latest_geo = session.scalar(
        select(LotGeoSnapshot)
        .where(
            LotGeoSnapshot.lot_id == lot_id,
            LotGeoSnapshot.centroid_lat.isnot(None),
            LotGeoSnapshot.centroid_lon.isnot(None),
        )
        .order_by(LotGeoSnapshot.id.desc())
    )
    lot = session.get(ProcessedLot, lot_id)
    if lot is None or latest_geo is None:
        return None
    source_rows = list(session.execute(
        select(ProcessedLot, SourceLot)
        .join(SourceLot, SourceLot.processed_lot_id == ProcessedLot.id)
        .where(or_(ProcessedLot.id == lot_id, ProcessedLot.duplicate_of_id == lot_id))
        .order_by(ProcessedLot.id, SourceLot.id)
    ))
    return _map_lot_payload(lot, latest_geo, source_rows)
