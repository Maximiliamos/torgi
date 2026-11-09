"""Read-only export of user-submitted lot issue reports for developer triage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from bankrotai.db import LotIssueReport, ProcessedLot, SessionLocal


def export_lot_issue_reports(*, limit: int = 200) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be in 1..1000")
    with SessionLocal() as session:
        reports = list(session.scalars(
            select(LotIssueReport)
            .order_by(LotIssueReport.id.desc())
            .limit(limit)
        ))
        items = []
        for report in reversed(reports):
            lot = session.get(ProcessedLot, report.lot_id)
            items.append({
                "report_id": report.id,
                "lot_id": report.lot_id,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                "content": report.content,
                "external_id": lot.external_id if lot else None,
                "source": lot.source if lot else None,
                "source_system": lot.source_system if lot else None,
                "title": lot.title if lot else None,
                "address": lot.address if lot else None,
                "cadastral_number": lot.cadastral_number if lot else None,
                "start_price": str(lot.start_price) if lot and lot.start_price is not None else None,
                "current_price": str(lot.current_price) if lot and lot.current_price is not None else None,
                "auction_status": lot.auction_status if lot else None,
                "application_deadline": (
                    lot.application_deadline.isoformat()
                    if lot and lot.application_deadline else None
                ),
                "auction_at": lot.auction_at.isoformat() if lot and lot.auction_at else None,
                "lot_url": lot.lot_url if lot else None,
            })
    return {"count": len(items), "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = export_lot_issue_reports(limit=args.limit)
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
