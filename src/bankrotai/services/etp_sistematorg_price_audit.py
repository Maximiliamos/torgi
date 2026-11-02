"""Operator read-only probe: can Sistematorg trade pages prove individual lot prices?

Sistematorg's catalogue is trade-level, not a per-lot inventory. This probe
does not mutate PostgreSQL or publish map points. It samples only procedures
explicitly containing one lot, where a labelled detail price is attributable
to that single lot.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from bankrotai.connectors.etp_sources import EtpSearchFilters, SOURCE_CONNECTOR_TYPES


async def audit_price_evidence(
    *, max_details: int = 10, delay_seconds: float = 1.0,
    connector=None,
) -> dict:
    if type(max_details) is not int or not 1 <= max_details <= 20:
        raise ValueError("max_details must be in 1..20")
    if not 0.5 <= delay_seconds <= 10:
        raise ValueError("delay_seconds must be in 0.5..10")
    source = connector or SOURCE_CONNECTOR_TYPES["sistematorg.com"]()
    page = await source.search(EtpSearchFilters(page=1))
    counts: Counter[str] = Counter()
    for lot in page.items:
        counts["catalogue_trade_rows"] += 1
        trade_lots = lot.raw_data.get("reported_trade_lots")
        if trade_lots != 1:
            counts["multiple_or_unknown_lots"] += 1
            continue
        counts["single_lot_trade_rows"] += 1
        if counts["detail_attempts"] >= max_details:
            continue
        if counts["detail_attempts"]:
            await asyncio.sleep(delay_seconds)
        counts["detail_attempts"] += 1
        try:
            detail = await source.fetch_lot(lot.external_id)
        except Exception as exc:
            # Aggregated error classes only: no URLs, names or personal data.
            counts["detail_failure_" + type(exc).__name__] += 1
            continue
        if detail.start_price is not None or detail.current_price is not None:
            counts["single_lot_detail_with_labelled_price"] += 1
        else:
            counts["single_lot_detail_missing_labelled_price"] += 1
    return {
        "source": "sistematorg.com",
        "mode": "read_only_first_page_trade_price_evidence",
        **dict(counts),
        "coverage_complete": False,
        "multi_lot_prices_attributed": False,
        "production_database_writes": 0,
        "archival_allowed": False,
        "map_publication": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-details", type=int, default=10)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    report = asyncio.run(audit_price_evidence(
        max_details=args.max_details, delay_seconds=args.delay_seconds,
    ))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
