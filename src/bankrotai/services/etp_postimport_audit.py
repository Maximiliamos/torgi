"""Read-only, PII-free post-import acceptance audit for M-ETS and CDTRF.

This is not proof of publication in REG.RU S3: the current map dataset
version must still be checked against its published tile/API bundle.
"""
from __future__ import annotations

import argparse
import json
import re

from sqlalchemy import func, select

from bankrotai.db import (
    LotGeoSnapshot, LotPriceEvent, LotStatusHistory,
    MapDataset, ProcessedLot, SessionLocal, SourceLot,
)

APPROVED = ("m-ets.ru", "cdtrf.ru")


def inspect_import(session, source_id: str) -> dict:
    if source_id not in APPROVED:
        raise ValueError("Not an approved ETP audit source")
    source_count = session.scalar(
        select(func.count(SourceLot.id)).where(SourceLot.source_system == source_id)
    ) or 0
    processed_count = session.scalar(
        select(func.count(ProcessedLot.id)).where(ProcessedLot.source_system == source_id)
    ) or 0
    archived = session.scalar(
        select(func.count(ProcessedLot.id)).where(
            ProcessedLot.source_system == source_id, ProcessedLot.is_archived.is_(True),
        )
    ) or 0
    duplicates = session.scalar(
        select(func.count(ProcessedLot.id)).where(
            ProcessedLot.source_system == source_id,
            ProcessedLot.duplicate_of_id.is_not(None),
        )
    ) or 0
    located = session.scalar(
        select(func.count(ProcessedLot.id)).where(
            ProcessedLot.source_system == source_id,
            ProcessedLot.current_geo_lat.is_not(None),
            ProcessedLot.current_geo_lon.is_not(None),
        )
    ) or 0
    linked = session.scalar(
        select(func.count(SourceLot.id)).where(
            SourceLot.source_system == source_id,
            SourceLot.processed_lot_id.is_not(None),
        )
    ) or 0
    statuses = session.scalar(
        select(func.count(LotStatusHistory.id))
        .join(ProcessedLot, LotStatusHistory.lot_id == ProcessedLot.id)
        .where(ProcessedLot.source_system == source_id)
    ) or 0
    price_events = session.scalar(
        select(func.count(LotPriceEvent.id))
        .join(ProcessedLot, LotPriceEvent.lot_id == ProcessedLot.id)
        .where(ProcessedLot.source_system == source_id)
    ) or 0
    geo_snapshots = session.scalar(
        select(func.count(LotGeoSnapshot.id))
        .join(ProcessedLot, LotGeoSnapshot.lot_id == ProcessedLot.id)
        .where(ProcessedLot.source_system == source_id)
    ) or 0
    return {
        "source": source_id, "source_lots": int(source_count),
        "processed_lots": int(processed_count),
        "linked_source_lots": int(linked),
        "archived": int(archived),
        "cross_source_duplicates": int(duplicates),
        "with_coordinates": int(located),
        "geo_snapshots": int(geo_snapshots),
        "status_history_events": int(statuses),
        "price_events": int(price_events),
        "verified_new_lot_map_publication": False,
    }



def verify_expected_source_lots(session, source_id: str, ids: list[str]) -> list[dict]:
    """Read-only proof that this particular canary's source IDs have correct links.

    Prior source aggregate counts are not evidence that newly attempted IDs
    were actually saved. GEO and public map publication remain separate gates.
    """
    if (source_id not in APPROVED or not 1 <= len(ids) <= 2
            or len(ids) != len(set(ids))
            or not all(re.fullmatch(r"[A-Za-z0-9:.-]{1,100}", value) for value in ids)):
        raise ValueError("ETP canary audit requires one or two unique public source IDs")
    result = []
    for external_id in ids:
        row = session.execute(
            select(
                SourceLot.source_system, SourceLot.canonical_lot_id,
                SourceLot.processed_lot_id, SourceLot.cadastral_number,
                SourceLot.is_archived,
                ProcessedLot.source_system, ProcessedLot.external_id,
                ProcessedLot.cadastral_number, ProcessedLot.is_archived,
                ProcessedLot.auction_status,
                ProcessedLot.current_geo_lat, ProcessedLot.current_geo_lon,
            ).join(ProcessedLot, SourceLot.processed_lot_id == ProcessedLot.id)
            .where(SourceLot.source_system == source_id,
                   SourceLot.external_id == external_id)
        ).one_or_none()
        if (row is None or row[0] != source_id or not row[1] or not row[2]
                or not row[3] or bool(row[4]) or row[5] != source_id
                or row[6] != external_id or not row[7] or bool(row[8])
                or row[9] not in ("active", "scheduled")
                or row[3] != row[7]):
            raise RuntimeError(
                "ETP canary source ID lacks an active, same-source canonical/processed link"
            )
        result.append({
            "external_id": external_id, "source_link_verified": True,
            "geo_observed": row[10] is not None and row[11] is not None,
            "public_map_verified": False,
        })
    return result


def report(session) -> dict:
    current = session.scalar(
        select(MapDataset).where(MapDataset.is_current.is_(True))
    )
    return {
        "audit": "etp_db_acceptance_readonly",
        "sources": [inspect_import(session, source_id) for source_id in APPROVED],
        "current_map_dataset_version": current.version if current else None,
        "current_map_dataset_published": bool(current and current.published_at),
        "requires_external_map_tile_verification": True,
        "database_writes": 0,
    }


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--min-source-lots", type=int, default=0)
    args.add_argument("--source", choices=APPROVED, default=None)
    args.add_argument("--expected-external-id", action="append", default=[])
    ns = args.parse_args()
    if ns.min_source_lots < 0:
        args.error("Negative minimum is invalid")
    if ns.expected_external_id and ns.source is None:
        args.error("Expected external IDs require --source")
    with SessionLocal() as session:
        payload = report(session)
        payload["expected_canary_source_lots"] = (
            verify_expected_source_lots(
                session, ns.source, ns.expected_external_id,
            ) if ns.expected_external_id else []
        )
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if ns.min_source_lots and any(
        x["source_lots"] < ns.min_source_lots for x in payload["sources"]
        if ns.source is None or x["source"] == ns.source
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
