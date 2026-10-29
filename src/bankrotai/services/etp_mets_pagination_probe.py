"""Strictly bounded first-party M-ETS catalogue pagination evidence.

This is not a complete auction inventory and never reads individual lot details,
writes a database, modifies source states, or publishes map tiles.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Callable

import requests

from bankrotai.connectors.registry.mets_public import (
    APARTMENTS_URL, MAX_HTML_BYTES, MetsEvidenceError, discover_lot_urls,
)

REQUEST_TIMEOUT = (5, 25)


def scan_mets_pages(
    session: requests.Session, *, max_pages: int = 5,
    delay_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep,
) -> dict:
    if not 1 <= max_pages <= 5 or not 1 <= delay_seconds <= 10:
        raise ValueError("M-ETS survey accepts 1..5 pages with a 1..10 second pause")

    seen: set[str] = set()
    pages: list[dict] = []
    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(delay_seconds)
        url = APARTMENTS_URL if page == 1 else f"{APARTMENTS_URL}&page={page}"
        response = session.get(
            url, timeout=REQUEST_TIMEOUT, allow_redirects=False,
            headers={"User-Agent": "TORGI-METS-readonly-page-audit/1", "Accept": "text/html"},
        )
        if response.status_code != 200 or len(response.content) > MAX_HTML_BYTES:
            raise MetsEvidenceError("M-ETS catalogue response failed status or size limit")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise MetsEvidenceError("M-ETS catalogue response is not HTML")

        urls = discover_lot_urls(response.text, limit=50)
        ids = {link.rsplit("/", 1)[-1] for link in urls}
        if not ids and page == 1:
            raise MetsEvidenceError("M-ETS first page has no verifiable lot identities")
        if ids & seen:
            raise MetsEvidenceError("M-ETS catalogue returns overlapping lot identities across pages")
        seen.update(ids)
        pages.append({
            "page": page,
            "catalogue_links": len(ids),
            "distinct_from_previous_pages": True,
        })
        if not ids:
            break

    return {
        "probe": "mets_official_catalogue_bounded_pagination",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "pages_scanned": len(pages),
        "unique_catalogue_lot_ids": len(seen),
        "pages": pages,
        "complete_source_inventory": False,
        "detail_validation_proven": False,
        "database_writes": 0,
        "archival_allowed": False,
        "map_publication": False,
    }


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--max-pages", type=int, default=5)
    cli.add_argument("--delay-seconds", type=float, default=1.0)
    opts = cli.parse_args()
    with requests.Session() as session:
        report = scan_mets_pages(
            session, max_pages=opts.max_pages, delay_seconds=opts.delay_seconds,
        )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
