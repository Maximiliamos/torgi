"""Read-only export of user-submitted lot issue reports for developer triage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from bankrotai.db import LotIssueReport, ProcessedLot, SessionLocal, SourceLot


_DEBUG_RAW_KEYS = (
    "raw_endpoint",
    "transport",
    "listing_page",
    "procedure_id",
    "trade_id",
    "lot_number",
    "reported_trade_lots",
    "individual_lot_identity_verified",
    "per_lot_detail_verified",
    "price_unverified",
    "status_unverified",
    "detail_enrichment_status",
    "detail_enrichment_version",
    "detail_identity_verified",
    "detail_start_price_verified",
    "detail_current_price_verified",
    "detail_status_verified",
    "detail_dates_verified",
    "listing_price_used_as_fallback",
)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value) -> str | None:
    return str(value) if value is not None else None


def _latest_source_lot(session, processed_lot_id: int) -> SourceLot | None:
    return session.scalar(
        select(SourceLot)
        .where(SourceLot.processed_lot_id == processed_lot_id)
        .order_by(SourceLot.last_seen_at.desc(), SourceLot.id.desc())
        .limit(1)
    )


def export_lot_issue_reports(*, limit: int = 200) -> dict:
    """Return a bounded read-only developer snapshot.

    ProcessedLot intentionally contains only public/current projection fields.
    Source-specific dates and raw parser evidence live on SourceLot, so the
    export joins that model instead of assuming those attributes exist on the
    projection row.
    """
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
            source_lot = _latest_source_lot(session, report.lot_id) if lot else None
            raw = source_lot.raw_data if source_lot and isinstance(source_lot.raw_data, dict) else {}
            items.append({
                "report_id": report.id,
                "lot_id": report.lot_id,
                "user_id": report.user_id,
                "created_at": _iso(report.created_at),
                "content": report.content,
                "external_id": lot.external_id if lot else None,
                "source": lot.source if lot else None,
                "source_system": lot.source_system if lot else None,
                "title": lot.title if lot else None,
                "description": lot.description if lot else None,
                "region_code": lot.region_code if lot else None,
                "region_name": lot.region_name if lot else None,
                "address": lot.address if lot else None,
                "cadastral_number": lot.cadastral_number if lot else None,
                "start_price": _money(lot.start_price) if lot else None,
                "current_price": _money(lot.current_price) if lot else None,
                "auction_status": lot.auction_status if lot else None,
                "is_archived": lot.is_archived if lot else None,
                "closed_at": _iso(lot.closed_at) if lot else None,
                "current_geo_lat": lot.current_geo_lat if lot else None,
                "current_geo_lon": lot.current_geo_lon if lot else None,
                "current_geo_source": lot.current_geo_source if lot else None,
                "current_geo_confidence": lot.current_geo_confidence if lot else None,
                "lot_url": lot.lot_url if lot else None,
                "source_url": lot.source_url if lot else None,
                "source_status": source_lot.source_status if source_lot else None,
                "application_start_at": _iso(source_lot.application_start_at) if source_lot else None,
                "application_deadline": _iso(source_lot.application_deadline) if source_lot else None,
                "auction_at": _iso(source_lot.auction_at) if source_lot else None,
                "auction_timezone": source_lot.auction_timezone if source_lot else None,
                "source_lot_is_active": source_lot.is_active if source_lot else None,
                "source_lot_is_archived": source_lot.is_archived if source_lot else None,
                "source_lot_archive_reason": source_lot.archive_reason if source_lot else None,
                "raw_debug": {
                    key: raw.get(key)
                    for key in _DEBUG_RAW_KEYS
                    if key in raw
                },
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
