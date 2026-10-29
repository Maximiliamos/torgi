"""S2/S3 read-only verification of bounded M-ETS/CDTRF incremental windows.

Runs five public catalogue pages in memory. Rejects repeated source IDs
across *any* pages (not just adjacent pages), incomplete metadata, truncated
pagination and invalid cards. It never opens a database session, updates
source state, reconciles missing lots or claims complete catalogue coverage.
"""
from __future__ import annotations

import argparse
import json
from typing import Callable

from bankrotai.connectors.base import ConnectorPage
from bankrotai.connectors.registry.etp_incremental import (
    EtpIncrementalFilters, _property_ready, incremental_cdtrf_search,
    incremental_mets_search,
)
from bankrotai.connectors.registry.mets_public import MetsPublicPilot

APPROVED = ("m-ets.ru", "cdtrf.ru")


def verify_window(
    source: str,
    fetch_page: Callable[[EtpIncrementalFilters, str | None], ConnectorPage],
    *,
    pages: int = 5,
    details_per_page: int = 2,
) -> dict:
    """Return aggregate proof, or fail closed on any inconsistent page."""
    filters = EtpIncrementalFilters(
        source, max_detail_requests=details_per_page, max_pages=pages,
    )
    seen: set[str] = set()
    cursor: str | None = None
    catalogued = 0
    rejected = 0
    for number in range(1, pages + 1):
        result = fetch_page(filters, cursor)
        if (
            not isinstance(result.metadata, dict)
            or result.metadata.get("complete_source_inventory") is not False
            or result.metadata.get("safe_for_archival") is not False
            or result.metadata.get("progress_current") != number
            or result.metadata.get("progress_total") != pages
        ):
            raise RuntimeError("ETP page has incomplete or misleading coverage metadata")
        if len(result.items) > details_per_page:
            raise RuntimeError("ETP page exceeds its verified detail limit")
        # A catalogue with more candidate IDs than the requested detail limit
        # is still partial; never imply that all catalogued IDs were validated.
        catalogue_items = result.metadata.get("catalogue_lots")
        if type(catalogue_items) is not int or catalogue_items < len(result.items):
            raise RuntimeError("ETP catalogue count is not evidenced")
        catalogued += catalogue_items
        detail_rejected = result.metadata.get("details_rejected")
        if type(detail_rejected) is not int or detail_rejected < 0:
            raise RuntimeError("ETP rejected-detail count is not evidenced")
        rejected += detail_rejected
        for lot in result.items:
            identity = str(getattr(lot, "external_id", "") or "").strip()
            if (getattr(lot, "source_system", None) != source
                    or not identity or identity in seen or not _property_ready(lot)):
                raise RuntimeError("ETP has repeated or unverified property across pages")
            seen.add(identity)
        expected_next_page = number + 1
        if number < pages:
            if (not isinstance(result.next_cursor, str)
                    or not result.next_cursor.startswith(f"{expected_next_page}:")):
                raise RuntimeError("ETP catalogue pagination ended prematurely")
            cursor = result.next_cursor
        elif result.next_cursor is not None:
            raise RuntimeError("ETP five-page window leaked an unbounded cursor")
    return {
        "source": source, "read_only": True,
        "no_database_writes": True, "pages_verified": pages,
        "catalogue_candidates_seen": catalogued,
        "verified_unique_property_details": len(seen),
        "details_rejected": rejected,
        "complete_source_inventory": False, "safe_for_archival": False,
        "reconcile_missing": False, "map_publication_verified": False,
    }


def read_only_probe(source: str) -> dict:
    if source not in APPROVED:
        raise ValueError("Unapproved ETP source")
    if source == "m-ets.ru":
        pilot = MetsPublicPilot()
        return verify_window(
            source, lambda f, c: incremental_mets_search(pilot, f, c),
        )
    return verify_window(source, lambda f, c: incremental_cdtrf_search(f, c))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=APPROVED, required=True)
    args = parser.parse_args()
    print(json.dumps(read_only_probe(args.source), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
