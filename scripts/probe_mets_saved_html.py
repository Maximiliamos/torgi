"""Offline verification of a saved M-ETS lot and optional search-page HTML.

Never reads browser cookies, performs HTTP requests, or writes to TORGI DB.
Does not export full lot descriptions, debtors' names or contact information.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bs4 import BeautifulSoup

from bankrotai.connectors.registry.mets_public import (
    BASE, MetsEvidenceError, discover_lot_urls, parse_mets_lot,
)

MAX_HTML_BYTES = 2_000_000


def _read(path: Path) -> str:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_HTML_BYTES:
        raise ValueError(f"Expected existing M-ETS HTML <=2MB: {path}")
    return path.read_text(encoding="utf-8-sig")


def _observed_page_type(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('meta[property="og:url"]')
    if canonical and canonical.get("content") == BASE + "/":
        return "homepage_not_catalogue"
    return "unverified_search"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", type=Path, required=True)
    ap.add_argument("--detail-url", required=True)
    ap.add_argument("--search-html", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    lot = parse_mets_lot(_read(args.detail), args.detail_url)
    result = {
        "source": "m-ets.ru",
        "detail": {
            "external_id": lot.external_id,
            "procedure_number": lot.procedure_number,
            "initial_price_rub": lot.start_price,
            "current_price_rub": lot.current_price,
            "source_status_class": lot.auction_status,
            "source_status_text": lot.raw_data.get("source_status_text"),
            "application_deadline": (
                lot.application_deadline.isoformat() if lot.application_deadline else None
            ),
            "cadastral_number": lot.cadastral_number,
            "mentions_pledge": lot.raw_data.get("mentions_pledge"),
            "detail_url": lot.lot_url,
        },
        "search_snapshot_type": None,
        "search_observed_links": [],
        "search_complete": False,
        "live_access_verified": False,
        "ready_for_ingestion": False,
        "safe_for_archival": False,
        "production_writes": 0,
    }
    if args.search_html:
        search_html = _read(args.search_html)
        kind = _observed_page_type(search_html)
        result["search_snapshot_type"] = kind
        if kind == "homepage_not_catalogue":
            result["search_error"] = "Saved HTML is the homepage, not category search."
        else:
            # URL discovery alone cannot establish full coverage or real paging.
            try:
                result["search_observed_links"] = discover_lot_urls(search_html, limit=50)
            except MetsEvidenceError:
                result["search_error"] = "No verifiable public lot links"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"M-ETS {lot.external_id}: initial={lot.start_price}, "
        f"status={lot.auction_status}, search={result['search_snapshot_type']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
