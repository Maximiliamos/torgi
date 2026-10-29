"""Bounded live ETP reachability probe for a home-worker container.

No database writes, no archival or publication; prints only metadata counts.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import EtpIncrementalFilters


async def run_probe() -> dict:
    results = []
    for source_id in ("m-ets.ru", "cdtrf.ru"):
        result: dict = {
            "source": source_id, "healthy": False,
            "catalogue_lots": None, "validated_details": 0,
            "rejected_details": None, "detail_attempts": None,
            "rejection_codes": {}, "error_type": None,
            "database_writes": 0, "archival_allowed": False,
        }
        try:
            connector = connector_registry.create(source_id)
            page = await asyncio.wait_for(
                connector.search(
                    EtpIncrementalFilters(source_id, max_detail_requests=5)
                ),
                timeout=160,
            )
            result["catalogue_lots"] = page.metadata.get("catalogue_lots")
            result["validated_details"] = len(page.items)
            result["rejected_details"] = page.metadata.get("details_rejected")
            result["detail_attempts"] = page.metadata.get("detail_attempts")
            result["rejection_codes"] = page.metadata.get("rejection_codes", {})
            result["healthy"] = bool(page.items)
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        results.append(result)
    return {
        "probe": "etp_home_readonly",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "production_database_writes": 0,
        "complete_source_inventory": False,
        "sources": results,
    }


def main() -> None:
    result = asyncio.run(run_probe())
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if not all(source["healthy"] for source in result["sources"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
