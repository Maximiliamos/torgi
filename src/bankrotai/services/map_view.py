from __future__ import annotations

from collections import defaultdict
from datetime import datetime
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
) -> dict:
    latest_geo = (
        select(LotGeoSnapshot.lot_id, func.max(LotGeoSnapshot.id).label("geo_id"))
        .where(LotGeoSnapshot.centroid_lat.isnot(None), LotGeoSnapshot.centroid_lon.isnot(None))
        .group_by(LotGeoSnapshot.lot_id)
        .subquery()
    )
    statement = (
        select(ProcessedLot, LotGeoSnapshot)
        .join(latest_geo, latest_geo.c.lot_id == ProcessedLot.id)
        .join(LotGeoSnapshot, LotGeoSnapshot.id == latest_geo.c.geo_id)
        .where(ProcessedLot.duplicate_of_id.is_(None))
        .order_by(ProcessedLot.last_update.desc())
        .limit(limit)
    )
    if city_slug:
        statement = statement.where(ProcessedLot.region_slug.in_(get_region_query_values(city_slug)))
    if not include_archived:
        statement = statement.where(ProcessedLot.is_archived.is_(False))
    rows = session.execute(statement).all()

    primary_ids = [lot.id for lot, _geo in rows]
    source_map: dict[int, list[tuple[ProcessedLot, SourceLot]]] = defaultdict(list)
    if primary_ids:
        source_rows = session.execute(
            select(ProcessedLot, SourceLot)
            .join(SourceLot, SourceLot.processed_lot_id == ProcessedLot.id)
            .where(or_(ProcessedLot.id.in_(primary_ids), ProcessedLot.duplicate_of_id.in_(primary_ids)))
            .order_by(ProcessedLot.id, SourceLot.id)
        )
        for processed, source_lot in source_rows:
            source_map[processed.duplicate_of_id or processed.id].append((processed, source_lot))

    items = [_map_lot_payload(lot, geo, source_map[lot.id]) for lot, geo in rows]
    return {"items": items, "total": len(items)}
