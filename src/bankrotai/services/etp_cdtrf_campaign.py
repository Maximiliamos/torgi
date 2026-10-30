"""One-source CDTRF import -> geocode -> map operator campaign.

Entry point for a trusted Home worker only. Never called from the public API,
default scheduler or source-pilot preview. A verified backup + explicit
operator flags are mandatory at the source_full_specs write boundary.

This campaign can report unlocated lots; it must never invent coordinates.
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import func, or_, select

from bankrotai.db import ProcessedLot, SessionLocal
from bankrotai.services.ingestion import (
    NationwideIngestionService, run_nationwide_sync, source_full_specs,
)

SOURCE = "cdtrf.ru"


def snapshot() -> dict[str, int]:
    with SessionLocal() as session:
        source = ProcessedLot.source_system == SOURCE
        active = (
            source
            & ProcessedLot.auction_status.in_(("active", "scheduled"))
            & ProcessedLot.is_archived.is_(False)
            & ProcessedLot.duplicate_of_id.is_(None)
        )
        has_geo = (
            ProcessedLot.current_geo_lat.is_not(None)
            & ProcessedLot.current_geo_lon.is_not(None)
        )
        return {
            "source_records": int(session.scalar(
                select(func.count(ProcessedLot.id)).where(source)
            ) or 0),
            "active_primary": int(session.scalar(
                select(func.count(ProcessedLot.id)).where(active)
            ) or 0),
            "with_coordinates": int(session.scalar(
                select(func.count(ProcessedLot.id)).where(active, has_geo)
            ) or 0),
            "without_coordinates": int(session.scalar(
                select(func.count(ProcessedLot.id)).where(active, ~has_geo)
            ) or 0),
        }


def run_import() -> dict:
    specs = source_full_specs(SOURCE)  # requires 3 operator + backup flags
    service = NationwideIngestionService(SessionLocal)
    run_id = service.create_run(
        triggered_by="cdtrf_operator_campaign",
        trigger_type="manual_source_full",
        total_sources=1,
    )
    result = run_nationwide_sync(SessionLocal, run_id, specs)
    source = result.get("sources", [{}])[0]
    payload = {"run_id": run_id, "status": result.get("status"),
               "source": source, "after_import": snapshot()}
    if (result.get("status") != "success"
            or source.get("source_system") != SOURCE
            or source.get("status") != "success"
            or int(source.get("pages_scanned") or 0) < 1
            or int(source.get("items_failed") or 0) > 0):
        raise RuntimeError("CDTRF operator import failed or was incomplete: " + json.dumps(
            payload, ensure_ascii=False, default=str,
        ))
    if int(source.get("items_archived") or 0) != 0:
        raise RuntimeError("CDTRF source unexpectedly archived lots")
    return payload


def geocode_source(*, batch_size: int = 250) -> dict:
    """One bounded attempt for each CDTRF lot missing a genuine coordinate.

    GEO retries/network waits remain in the normal durable GEO queue, and
    are explicitly reported as unresolved rather than pretending to geocode
    every lot on the first pass.
    """
    if not 1 <= batch_size <= 500:
        raise ValueError("Invalid geocoder batch size")
    from bankrotai.services.geo_backfill import geocode_pending_lots

    with SessionLocal() as session:
        ids = list(session.scalars(
            select(ProcessedLot.id)
            .where(
                ProcessedLot.source_system == SOURCE,
                ProcessedLot.auction_status.in_(("active", "scheduled")),
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                or_(
                    ProcessedLot.current_geo_lat.is_(None),
                    ProcessedLot.current_geo_lon.is_(None),
                ),
            ).order_by(ProcessedLot.id)
        ).all())
    batches = []
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        response = geocode_pending_lots(
            SessionLocal, limit=len(chunk), lot_ids=chunk,
            refresh_strategy=False,
        )
        if response.get("status") in ("busy", "paused"):
            raise RuntimeError("CDTRF source-specific GEO campaign blocked by existing GEO work")
        batches.append({
            "requested_ids": len(chunk),
            "queued": response.get("queued", 0),
            "geocoded": response.get("geocoded", 0),
            "failed": response.get("failed", 0),
        })
    return {"requested": len(ids), "batches": batches, "after_geo": snapshot()}


def publish_map() -> dict:
    from bankrotai.services.map_builder import build_map_dataset

    before = snapshot()
    if before["with_coordinates"] == 0:
        raise RuntimeError("CDTRF has no verified geocoded points; refusing empty map claim")
    publication = build_map_dataset(SessionLocal)
    if publication.get("build_status") != "success":
        raise RuntimeError("Map publication failed")
    return {"before_publication": before, "map": publication, "after_publication": snapshot()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("import", "geocode", "map", "audit"))
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if args.stage == "import":
        out = run_import()
    elif args.stage == "geocode":
        out = geocode_source(batch_size=args.batch_size)
    elif args.stage == "map":
        out = publish_map()
    else:
        out = {"source": SOURCE, "snapshot": snapshot()}
    print(json.dumps(out, ensure_ascii=False, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
