"""Source-scoped GEO and map publication for previously persisted verified ETP canaries.

This module never fetches source pages or persists auction records. The paired
operator workflow first requires exact-SHA acceptance, a restore-verified backup,
a collision review and an atomic <=2-lot import.
"""
from __future__ import annotations

import argparse
import json
import re

from sqlalchemy import select

from bankrotai.db import ProcessedLot, SessionLocal

APPROVED = frozenset({"m-ets.ru", "cdtrf.ru"})
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9:._-]{1,100}$")


def source_snapshot(source: str, external_ids: tuple[str, ...]) -> dict:
    """Only count the precise verified identities from this operator run."""
    if source not in APPROVED or not 1 <= len(external_ids) <= 2:
        raise ValueError("Source GEO campaign requires one or two approved identities")
    if len(set(external_ids)) != len(external_ids) or any(
        not EXTERNAL_ID.fullmatch(external_id) for external_id in external_ids
    ):
        raise ValueError("Source GEO campaign has invalid or duplicate identities")
    with SessionLocal() as session:
        rows = session.scalars(
            select(ProcessedLot).where(
                ProcessedLot.source_system == source,
                ProcessedLot.external_id.in_(external_ids),
            )
        ).all()
        if len(rows) != len(external_ids):
            raise RuntimeError("Imported source identities are not in PostgreSQL")
        if any(
            row.is_archived or row.duplicate_of_id is not None
            or row.auction_status not in ("active", "scheduled")
            or not row.cadastral_number
            for row in rows
        ):
            raise RuntimeError("Source GEO campaign found unverified or archived lot")
        missing_ids = [
            row.id for row in rows
            if row.current_geo_lat is None or row.current_geo_lon is None
        ]
        mapped_ids = [
            row.id for row in rows
            if row.current_geo_lat is not None and row.current_geo_lon is not None
        ]
        return {
            "source": source,
            "expected": len(external_ids),
            "source_records_verified": len(rows),
            "with_coordinates": len(mapped_ids),
            "without_coordinates": len(missing_ids),
            "geo_pending_lot_ids": missing_ids,
            "mapped_lot_ids": mapped_ids,
        }


def geocode(source: str, external_ids: tuple[str, ...]) -> dict:
    before = source_snapshot(source, external_ids)
    work = {"status": "not_needed"}
    if before["geo_pending_lot_ids"]:
        from bankrotai.services.geo_backfill import geocode_pending_lots

        ids = before["geo_pending_lot_ids"]
        work = geocode_pending_lots(
            SessionLocal, limit=len(ids), lot_ids=ids, refresh_strategy=False
        )
        if work.get("status") in ("busy", "paused", "failed", "error"):
            raise RuntimeError("ETP GEO queue could not process the exact source identities")
    after = source_snapshot(source, external_ids)
    if after["with_coordinates"] < 1:
        raise RuntimeError(
            "ETP has no reliable source-scoped GEO coordinates; refusing publication"
        )
    return {"stage": "geocode", "source": source, "before": before,
            "geocoder_status": work.get("status"), "after": after}


def publish(source: str, external_ids: tuple[str, ...]) -> dict:
    before = source_snapshot(source, external_ids)
    if before["with_coordinates"] < 1:
        raise RuntimeError("ETP map publication requires genuine source coordinates")
    from bankrotai.services.map_builder import build_map_dataset

    outcome = build_map_dataset(SessionLocal)
    if outcome.get("build_status") != "success":
        raise RuntimeError("Map publication failed: no current dataset confirmed")
    after = source_snapshot(source, external_ids)
    return {"stage": "map", "source": source, "before": before, "after": after,
            "map_build_status": outcome.get("build_status")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=("audit", "geocode", "map"))
    ap.add_argument("--source", choices=sorted(APPROVED), required=True)
    ap.add_argument("--external-id", action="append", required=True)
    args = ap.parse_args()
    ids = tuple(args.external_id)
    if args.stage == "audit":
        report = source_snapshot(args.source, ids)
    elif args.stage == "geocode":
        report = geocode(args.source, ids)
    else:
        report = publish(args.source, ids)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
