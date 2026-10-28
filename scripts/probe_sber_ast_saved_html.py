"""Inspect saved Sberbank-AST UTP property sale and general procurements HTML.

Offline only: no network, no cookies, no database and no credential uploads.
The output excludes personal contact details, documents and full lot titles.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bankrotai.connectors.registry.sber_ast_property import (
    inspect_united_purchase_list,
    parse_property_detail,
)


def _html(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 3_000_000:
        raise ValueError(f"Expected local HTML file <=3MB: {path}")
    return path.read_text(encoding="utf-8-sig")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--united-list", type=Path, required=True)
    p.add_argument("--detail", type=Path, required=True)
    p.add_argument("--detail-url", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    list_info = inspect_united_purchase_list(_html(args.united_list))
    detail = parse_property_detail(_html(args.detail), args.detail_url)
    safe_fields = (
        "source", "source_section", "purchase_id", "trade_id", "lot_id",
        "lot_number", "external_id", "trade_type", "source_status_text",
        "property_type", "region", "initial_price_rub", "minimum_price_rub",
        "deposit_rub", "area_sqm", "land_area_sqm", "application_start",
        "application_end", "application_review", "auction_start",
        "price_periods", "detail_url", "verified_saved_html",
        "pagination_verified", "source_complete", "ready_for_ingestion",
    )
    report = {
        "general_procurement_page": list_info,
        "property_sale_detail": {key: detail[key] for key in safe_fields},
        "source_completeness_proven": False, "database_writes": 0,
        "live_network_verified": False, "safe_for_archival": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Sber-AST sale {detail['trade_id']} lot {detail['lot_id']}: "
        f"{len(detail['price_periods'])} price periods, report {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
