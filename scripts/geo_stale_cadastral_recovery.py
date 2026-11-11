"""Bounded stale cadastral no-match recovery.

Dry-run by default. Every accepted lot must be independently re-proven by live
NSPD with the exact same cadastral number and usable coordinates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from bankrotai.db import SessionLocal
from bankrotai.geo import CADASTRAL_GEOCODER
from bankrotai.services.stale_cadastral_recovery import recover_stale_cadastral_live_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot-id", action="append", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def live_nspd_probe(cadastral: str):
    found = CADASTRAL_GEOCODER._search_nspd_geoportal(cadastral)
    if found is None:
        return None, "live_nspd_no_match"
    observed = re.sub(r"\s+", "", str(found.cadastral_number or ""))
    expected = re.sub(r"\s+", "", cadastral)
    if observed != expected:
        return None, "live_nspd_identity_mismatch"
    if found.lat is None or found.lon is None:
        return None, "live_nspd_no_coordinates"
    return found, "exact_match"


def main() -> dict:
    args = parse_args()
    ids = list(dict.fromkeys(int(value) for value in args.lot_id))
    if not 1 <= len(ids) <= 20:
        raise ValueError("stale cadastral recovery canary is bounded to 1..20 exact lot ids")

    with SessionLocal() as session:
        result = recover_stale_cadastral_live_results(
            session,
            lot_ids=ids,
            live_result_probe=live_nspd_probe,
            apply=bool(args.apply),
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
        return {
            "report": "stale_cadastral_geo_recovery",
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
                "report": "stale_cadastral_geo_recovery",
                "error_type": type(exc).__name__,
            }),
            file=sys.stderr,
        )
        raise
