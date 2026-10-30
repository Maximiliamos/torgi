"""One-time bounded public ETP QA campaign with optional exact-cadastre GEO.

No production DB, reconciliation, archival, queue or map dataset changes.
Emits safe aggregate counts; full candidates are available to an authenticated
admin in the source-by-source QA map on sterdez.online.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from bankrotai.services.etp_qa_map import SUPPORTED, preview


async def run_all(*, pages: int, per_page: int, geo_limit: int) -> dict:
    reports = []
    sources = sorted(SUPPORTED)
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        try:
            result = await preview(
                source, pages=pages, per_page=per_page,
                geo_limit=geo_limit,
            )
            entry = {
                "source": source,
                "status": result["status"],
                "pages_fetched": result["pages_fetched"],
                "candidates": result["candidate_count"],
                "mapped": result["mapped_count"],
                "error_categories": result["errors"],
            }
        except Exception as exc:
            entry = {
                "source": source, "status": "failed",
                "pages_fetched": 0, "candidates": 0, "mapped": 0,
                "error_categories": [type(exc).__name__],
            }
        reports.append(entry)
        print(json.dumps({
            "qa_progress": entry,
            "completed_sources": index,
            "total_sources": total,
            "percent": round(index / total * 100, 1),
        }, ensure_ascii=False), flush=True)
    return {
        "report": "etp_manual_qa_multi_source",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "qa_only": True,
        "complete_source_inventory": False,
        "source_count": len(reports),
        "database_writes": 0,
        "production_map_writes": 0,
        "archival_enabled": False,
        "candidates": sum(r["candidates"] for r in reports),
        "mapped": sum(r["mapped"] for r in reports),
        "sources": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=2, choices=(1, 2))
    parser.add_argument("--per-page", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--geo-limit", type=int, default=1, choices=(0, 1, 2))
    args = parser.parse_args()
    result = asyncio.run(run_all(
        pages=args.pages, per_page=args.per_page,
        geo_limit=args.geo_limit,
    ))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
