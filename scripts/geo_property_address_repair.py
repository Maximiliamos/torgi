"""Bounded operator entrypoint for historical property-address GEO recovery.

Dry-run by default. Apply is limited to explicitly supplied ProcessedLot IDs.
"""
from __future__ import annotations

import argparse
import json
import sys

from bankrotai.db import SessionLocal
from bankrotai.services.property_address_repair import repair_property_addresses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot-id", action="append", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> dict:
    args = parse_args()
    ids = list(dict.fromkeys(int(value) for value in args.lot_id))
    if not 1 <= len(ids) <= 100:
        raise ValueError("property-address repair is bounded to 1..100 exact lot ids")

    with SessionLocal() as session:
        result = repair_property_addresses(
            session,
            lot_ids=ids,
            apply=bool(args.apply),
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
        return {
            "report": "property_address_geo_repair",
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
                "report": "property_address_geo_repair",
                "error_type": type(exc).__name__,
            }),
            file=sys.stderr,
        )
        raise
