"""Full read-only scan of the selected official CDTRF catalogue.

Unlike a bounded five-page canary, this scans every declared page. It does
NOT fetch verified lot details, write to the database, reconcile missing lots,
archive anything, or claim a point-in-time snapshot of a changing catalogue.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from math import ceil
from typing import Callable
from urllib.parse import urlencode

import requests

from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.services.etp_cdtrf_canary import API, MAX_RESPONSE, REQUEST_TIMEOUT

# The supplied 30 October 2026 catalogue HTML lists 20 / 50 / 100.
# There is no confirmed server support for PageSize=1000.
CDTRF_PAGE_SIZES = frozenset({20, 50, 100})
CDTRF_STATUS_PARAMS = (
    "Declare", "RecieveReq", "DefinePart", "TradeGo", "SummingUp"
)
CDTRF_PROPERTY_CATEGORIES = "21,22,23,31,32,33,34"
MAX_SCAN_PAGES = 10_000


class CdtrfCatalogueScanError(ValueError):
    """A full page traversal is not sufficiently proven."""


def _url(page: int, page_size: int) -> str:
    params = [(name, "true") for name in CDTRF_STATUS_PARAMS]
    params.extend([
        ("Categories", CDTRF_PROPERTY_CATEGORIES),
        ("PageSize", str(page_size)),
        ("PageNum", str(page)),
    ])
    return f"{API}?{urlencode(params)}"


def scan_complete_cdtrf_catalogue(
    session: requests.Session,
    *,
    page_size: int = 100,
    max_pages: int = MAX_SCAN_PAGES,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Read every page declared by CDTRF or fail without marking completion.

    The only accepted page sizes are the actual options shown on the
    user-provided public page. Any server-side cap that returns fewer rows
    than requested causes explicit failure, never a silently truncated scan.

    Concurrent catalogue changes can reorder results even if counts stay
    stable. Therefore the result NEVER permits deletion or missing-lot
    reconciliation; two stable cycles / provider snapshot would be needed
    before production acceptance.
    """
    if type(page_size) is not int or page_size not in CDTRF_PAGE_SIZES:
        raise CdtrfCatalogueScanError(
            "Unverified CDTRF PageSize; supported public UI values: 20, 50, 100"
        )
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_SCAN_PAGES:
        raise CdtrfCatalogueScanError("Unsafe max_pages")
    if not 0.5 <= delay_seconds <= 10:
        raise CdtrfCatalogueScanError("Delay must be 0.5..10 seconds")

    seen_ids: set[str] = set()
    page_report: list[dict[str, int]] = []
    initial_total: int | None = None
    pages_expected: int | None = None
    property_candidates = 0

    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(delay_seconds)
        response = session.get(
            _url(page, page_size),
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "TORGI-CDTRF-full-readonly-audit/1",
            },
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
            raise CdtrfCatalogueScanError("CDTRF API status/size failed")
        if "json" not in response.headers.get("Content-Type", "").casefold():
            raise CdtrfCatalogueScanError("CDTRF API response is not JSON")

        data = parse_cdtrf_api_page(
            response.json(), page_num=page, page_size=page_size
        )
        total = data["reported_total"]
        if initial_total is None:
            initial_total = total
            pages_expected = ceil(total / page_size) or 1
            if pages_expected > max_pages:
                raise CdtrfCatalogueScanError(
                    "Declared catalogue exceeds configured page budget"
                )
        elif total != initial_total:
            raise CdtrfCatalogueScanError(
                "CDTRF total changed during traversal; retry a new scan"
            )

        expected_count = min(
            page_size, max(0, initial_total - (page - 1) * page_size)
        )
        if data["observed_rows"] != expected_count:
            raise CdtrfCatalogueScanError(
                "Incomplete page or unsupported server PageSize; "
                "do not skip a page or mark the scan complete"
            )
        incoming = set(data["observed_lot_ids"])
        if incoming & seen_ids:
            raise CdtrfCatalogueScanError(
                "Overlapping CDTRF lot identities across catalogue pages"
            )
        seen_ids.update(incoming)
        property_candidates += len(data["property_candidates"])
        page_report.append({
            "page": page,
            "observed_rows": data["observed_rows"],
            "property_hint_candidates": len(data["property_candidates"]),
        })
        if page == pages_expected:
            break
    else:
        raise CdtrfCatalogueScanError("Page budget exhausted before last page")

    if initial_total is None or len(seen_ids) != initial_total:
        raise CdtrfCatalogueScanError("Unmatched observed and declared row count")
    digest = hashlib.sha256(
        "\n".join(sorted(seen_ids)).encode("ascii")
    ).hexdigest()
    return {
        "mode": "cdtrf_selected_catalogue_readonly_full_page_walk",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_size": page_size,
        "reported_total": initial_total,
        "pages_expected": pages_expected,
        "pages_scanned": len(page_report),
        "unique_catalogue_lot_ids": len(seen_ids),
        "property_hint_candidates": property_candidates,
        "selected_statuses": list(CDTRF_STATUS_PARAMS),
        "selected_categories": CDTRF_PROPERTY_CATEGORIES,
        "lot_ids_sha256": digest,
        "pages": page_report,
        "selected_catalogue_pages_observed": True,
        "complete_source_inventory": False,
        "stable_point_in_time_snapshot": False,
        "detail_validation_proven": False,
        "production_database_writes": 0,
        "map_publication": False,
        "archival_allowed": False,
        "reconcile_missing": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, choices=sorted(CDTRF_PAGE_SIZES),
                        default=100)
    parser.add_argument("--max-pages", type=int, default=MAX_SCAN_PAGES)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    with requests.Session() as session:
        report = scan_complete_cdtrf_catalogue(
            session, page_size=args.page_size,
            max_pages=args.max_pages, delay_seconds=args.delay_seconds,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
