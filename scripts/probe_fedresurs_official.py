"""Bounded, read-only pilot of the official EFRSB REST API.

Writes only an optional local diagnostic JSON report. Never modifies TORGI
PostgreSQL, schedules Celery jobs or publishes auction/map records.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bankrotai.connectors.registry.fedresurs_publications import (
    DEMO_URL,
    PRODUCTION_URL,
    FedresursPublicationsClient,
)


def report_for_period(client, *, kind, start, end, types, page_size, max_pages):
    result = client.collect_bounded(
        kind=kind,
        start_date=start,
        end_date=end,
        page_size=page_size,
        max_pages=max_pages,
        message_types=types,
    )
    events = result.pop("events")
    safe_rows = [
        {
            "guid": str(item.get("guid", "")),
            "number": item.get("number"),
            "type": item.get("type"),
            "datePublish": item.get("datePublish"),
            "tradeNumber": (item.get("trade") or {}).get("number")
            if isinstance(item.get("trade"), dict) else None,
            "tradeGuid": (item.get("trade") or {}).get("guid")
            if isinstance(item.get("trade"), dict) else None,
            "tradePlaceGuid": item.get("tradePlaceGuid"),
            "isLocked": item.get("isLocked"),
            "annulmentMessageGuid": item.get("annulmentMessageGuid"),
        }
        for item in events
    ]
    result["event_index"] = safe_rows
    result["category"] = "official_publication_evidence_not_sale_lots"
    result["data_written_to_torgi"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("demo", "production"), default="demo")
    parser.add_argument("--kind", choices=("messages", "trade-messages"), default="trade-messages")
    parser.add_argument("--start", required=True, help="Publication start date YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="Publication end date YYYY-MM-DD, inclusive")
    parser.add_argument("--types", default=None, help="Optional official message types, comma separated")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-network-access", action="store_true", required=True)
    args = parser.parse_args()

    client = FedresursPublicationsClient(
        base_url=DEMO_URL if args.environment == "demo" else PRODUCTION_URL
    )
    data = report_for_period(
        client, kind=args.kind, start=args.start, end=args.end,
        types=args.types, page_size=args.page_size, max_pages=args.max_pages,
    )
    data["environment"] = args.environment
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "environment": data["environment"],
                "kind": data["kind"],
                "reported_total": data["reported_total"],
                "collected": data["collected"],
                "complete": data["complete"],
                "data_written_to_torgi": False,
                "report": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
