"""Bounded, read-only official CDTRF API pagination evidence.

Never equate this five-page observation with a complete public catalogue or
delete/archive missing items. No lot detail, debtor PII or source URL in report.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Callable

import requests

from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.services.etp_cdtrf_canary import API, MAX_RESPONSE, REQUEST_TIMEOUT


def scan_cdtrf_pages(
    session: requests.Session, *, max_pages: int = 5,
    delay_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep,
) -> dict:
    if not 1 <= max_pages <= 5 or not 1 <= delay_seconds <= 10:
        raise ValueError("CDTRF pagination audit accepts 1..5 pages and 1..10 second pause")
    previous_seen: set[str] = set()
    per_page: list[dict] = []
    totals: list[int] = []
    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(delay_seconds)
        url = f"{API}?Declare=true&RecieveReq=true&PageSize=20&PageNum={page}"
        response = session.get(
            url, allow_redirects=False, timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": "TORGI-CDTRF-readonly-page-audit/1"},
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
            raise ValueError("CDTRF page HTTP status or response size untrusted")
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("CDTRF pagination endpoint did not return JSON")
        record = parse_cdtrf_api_page(response.json(), page_num=page, page_size=20)
        ids = set(record["observed_lot_ids"])
        if ids & previous_seen:
            raise ValueError("CDTRF API returned overlapping lot IDs across pages")
        previous_seen.update(ids)
        total = record.get("reported_total")
        if type(total) is int and total >= 0:
            totals.append(total)
        per_page.append({
            "page": page,
            "observed_rows": record["observed_rows"],
            "property_candidates": len(record["property_candidates"]),
            "distinct_from_previous_pages": True,
        })
        if record["observed_rows"] == 0:
            break
    return {
        "probe": "cdtrf_official_api_bounded_pagination",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "pages_scanned": len(per_page),
        "unique_catalogue_lot_ids": len(previous_seen),
        "property_hint_candidates": sum(p["property_candidates"] for p in per_page),
        "declared_total_range": [min(totals), max(totals)] if totals else None,
        "pages": per_page,
        "complete_source_inventory": False,
        "detail_validation_proven": False,
        "production_database_writes": 0,
        "map_publication": False,
        "archival_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    with requests.Session() as session:
        report = scan_cdtrf_pages(
            session, max_pages=args.max_pages, delay_seconds=args.delay_seconds,
        )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
