"""Safe, machine-readable evidence inventory for all 22 registered direct ETP profiles.

Does not return candidate titles, names, URLs, cookies, HTML, source payloads
or personal information. Catalogues are sampled once, with rate limiting.
No imports, DB writes, archival, or map publication are performed.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import requests

from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILES,
    batch_probe,
)

_READINESS = {
    # This survey alone cannot verify detail, fresh statuses or map ingestion.
    "existing_connector": ("not_assessed", "audit_existing_default_connector"),
    "needs_public_catalogue_contract": ("S0", "discover_official_public_catalogue_and_paging"),
    "candidates_unverified": ("S1", "verify_detail_identity_cadastral_price_and_status"),
    "no_matches_or_changed_html": ("S0", "review_listing_html_filters_and_first_page"),
    "blocked_or_parser_error": ("S0", "review_access_or_html_contract_without_bypass"),
}
_ALLOWED_STATES = frozenset(_READINESS)


def run_inventory(
    *, session: requests.Session, source_ids: tuple[str, ...] | None = None,
    limit: int = 5, delay_seconds: float = 1.0,
) -> dict:
    """Run the existing bounded first-party probe without retaining raw evidence."""
    evidence = batch_probe(
        session=session, ids=source_ids, limit=limit, delay_seconds=delay_seconds
    )
    sources: list[dict] = []
    for row in evidence["sources"]:
        source_id = row["source"]
        state = row["status"]
        if state not in _ALLOWED_STATES:
            raise ValueError("Unexpected ETP evidence state")
        count = len(row.get("candidates") or [])
        sources.append({
            "source_id": source_id,
            "strategy": next(p.strategy for p in PROFILES if p.source_id == source_id),
            "status": state,
            "readiness_stage": _READINESS[state][0],
            "next_required_action": _READINESS[state][1],
            "candidate_count": count,
            "detail_verified": False,
            "pagination_verified": False,
            "production_ingestion_enabled_by_inventory": False,
            "error_type": row.get("error_type") if state == "blocked_or_parser_error" else None,
        })
    return {
        "report": "etp_22_source_readonly_inventory",
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "registered_profile_count": len(PROFILES),
        "probed_source_count": len(sources),
        "complete_source_inventory": False,
        "production_database_writes": 0,
        "map_publication": False,
        "archival_allowed": False,
        "source_status_counts": {
            status: sum(item["status"] == status for item in sources)
            for status in sorted(_ALLOWED_STATES)
        },
        "sources": sources,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", action="append", dest="sources", default=None)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--delay-seconds", type=float, default=1.0)
    args = ap.parse_args()
    with requests.Session() as session:
        report = run_inventory(
            session=session,
            source_ids=tuple(args.sources) if args.sources else None,
            limit=args.limit,
            delay_seconds=args.delay_seconds,
        )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
