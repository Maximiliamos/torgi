"""Bounded operator canary for torgi.gov source-detail GEO recovery.

Default is dry-run. Apply is limited to explicitly supplied ProcessedLot IDs.
All source details are fetched and identity-validated before any DB mutation.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from bankrotai.db import ProcessedLot, SessionLocal
from bankrotai.scrapers import TorgiGovClient
from bankrotai.services.source_detail_geo_repair import repair_source_details_for_geo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot-id", action="append", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> dict:
    args = parse_args()
    ids = list(dict.fromkeys(int(value) for value in args.lot_id))
    if not 1 <= len(ids) <= 20:
        raise ValueError("torgi.gov detail repair is bounded to 1..20 exact lot ids")

    with SessionLocal() as session:
        rows = session.scalars(
            select(ProcessedLot).where(ProcessedLot.id.in_(ids))
        ).all()
        by_id = {int(row.id): row for row in rows}
        missing = [lot_id for lot_id in ids if lot_id not in by_id]
        if missing:
            raise RuntimeError(f"ProcessedLot ids not found: {missing}")
        wrong_source = [
            lot_id for lot_id in ids
            if by_id[lot_id].source_system != "torgi.gov.ru"
        ]
        if wrong_source:
            raise RuntimeError(f"Not torgi.gov ProcessedLot ids: {wrong_source}")

        client = TorgiGovClient(timeout=(10, 30), rate_limit=(0.4, 0.8))
        details = []
        # Fetch and verify every public detail before entering the write phase.
        for lot_id in ids:
            detail = client.fetch_lot_detail(by_id[lot_id].external_id)
            details.append((lot_id, detail))

        result = repair_source_details_for_geo(
            session,
            details=details,
            apply=bool(args.apply),
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
        return {
            "report": "torgi_gov_detail_geo_repair",
            "mode": "apply" if args.apply else "dry_run",
            "database_writes": result["updated"] if args.apply else 0,
            **result,
        }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(
            json.dumps({
                "report": "torgi_gov_detail_geo_repair",
                "error_type": type(exc).__name__,
            }),
            file=sys.stderr,
        )
        raise
