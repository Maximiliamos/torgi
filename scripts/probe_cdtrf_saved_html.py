"""Verify two saved CDTRF HTML snapshots locally, without any network requests.

This tool never touches TORGI DB, Redis, S3, Celery, or GitHub.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bankrotai.connectors.registry.cdtrf_public import (
    parse_catalogue,
    parse_detail,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--detail-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.catalog, args.detail):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError(f"Expected a local saved HTML snapshot <=2MB: {path}")
    listing = parse_catalogue(args.catalog.read_text(encoding="utf-8-sig"))
    lot = parse_detail(args.detail.read_text(encoding="utf-8-sig"), args.detail_url)
    present = any(row["trade_id"] == lot["trade_id"] for row in listing["page_observations"])
    result = {
        "source": "cdtrf.ru",
        "catalog_visible_cards": listing["visible_cards"],
        "catalog_accepted_cards": listing["accepted_cards"],
        "catalog_reported_total": listing["reported_total_lots"],
        "detail_present_in_catalog": present,
        "detail": lot,
        "complete_source_inventory": False,
        "production_writes": 0,
        "verified_live_network": False,
        "note": "Saved snapshot only; full pagination and current status need live verification",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"CDTRF parsed: {listing['accepted_cards']} catalogue cards; "
        f"detail trade={lot['trade_id']}, lot={lot['trade_lot_id']}; "
        f"report={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
