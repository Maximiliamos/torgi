"""Validate two saved RTS public HTML snapshots without internet or DB writes.

Report deliberately omits full descriptions, names, contacts and legal documents.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bankrotai.connectors.registry.rts_property_public import (
    parse_grid, parse_lot_detail,
)


def read_html(path: Path) -> str:
    if not path.is_file() or not 0 < path.stat().st_size <= 2_000_000:
        raise ValueError(f"Expected saved HTML <=2MB: {path}")
    return path.read_text(encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--detail-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    grid = parse_grid(read_html(args.grid))
    detail = parse_lot_detail(read_html(args.detail), args.detail_url)
    candidates = grid["property_candidates"]
    listed = next((x for x in candidates if x["lot_uuid"] == detail["lot_uuid"]), None)
    match = (
        listed is not None
        and listed["notice_number"] == detail["notice_number"]
        and listed["lot_number"] == detail["lot_number"]
        and listed["listing_price_rub"] == detail["initial_price_rub"]
    )
    if not match:
        raise ValueError("Detail absent from saved grid or stable source fields disagree")
    safe_detail_keys = (
        "source", "notice_number", "lot_number", "source_lot_code",
        "lot_uuid", "external_id", "category", "source_status_text",
        "regulation", "initial_price_rub", "current_price_rub",
        "cadastral_numbers", "notice_url", "detail_url",
        "application_countdown_date_text",
    )
    report = {
        "source": "rts-tender.ru", "grid_observed_cards": grid["observed_cards"],
        "grid_property_candidates": len(candidates),
        "grid_rejected": grid["rejected"],
        "grid_reported_total_label": grid["reported_total_label"],
        "detail_matches_listing": match,
        "detail": {key: detail[key] for key in safe_detail_keys},
        "live_network_verified": False, "full_pagination_verified": False,
        "source_completeness_proven": False, "production_writes": 0,
        "safe_for_archival": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"RTS: {grid['observed_cards']} cards, "
        f"{len(candidates)} property candidates, "
        f"detail={detail['lot_uuid']} matched: {match}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
