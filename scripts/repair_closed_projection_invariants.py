"""Repair archive flags only when both persisted source and projection are closed.

Default is a read-only preview. Apply requires the exact preview digest.
The operator must verify a restorable backup before invoking --apply.
No external status, ratings, notes, source facts or identities are changed.
"""
import argparse
import hashlib
import json

from sqlalchemy import select, text, update

from bankrotai.db import LotSyncRun, ProcessedLot, SessionLocal, SourceLot, utc_now


def execute(*, apply=False, expected_sha=None, rollback_only=False):
    with SessionLocal() as session:
        if apply:
            session.execute(text("SET LOCAL lock_timeout = '10s'"))
            if session.scalar(select(LotSyncRun.id).where(
                LotSyncRun.status.in_(["queued", "running"])
            ).limit(1)):
                raise RuntimeError("Active ingestion; defer invariant repair")
        else:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        query = select(
            ProcessedLot.id, ProcessedLot.source_system, ProcessedLot.external_id,
            ProcessedLot.review_status, ProcessedLot.archived_at, ProcessedLot.closed_at,
            SourceLot.id.label("source_lot_id"),
        ).join(SourceLot, SourceLot.processed_lot_id == ProcessedLot.id).where(
            ProcessedLot.auction_status == "closed", ProcessedLot.is_archived.is_(False),
            SourceLot.source_status == "closed", SourceLot.is_archived.is_(True),
        ).order_by(ProcessedLot.id)
        if apply:
            query = query.with_for_update(of=(ProcessedLot, SourceLot))
        items = [dict(row) for row in session.execute(query).mappings()]
        digest = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()
        if apply and (not expected_sha or digest != expected_sha):
            raise RuntimeError("Exact preview cohort/state changed; obtain a new preview")
        updated = 0
        if apply:
            now = utc_now()
            for offset in range(0, len(items), 500):
                ids = [row["id"] for row in items[offset:offset + 500]]
                # Preserve existing bookkeeping dates and all user-owned fields.
                from sqlalchemy import func
                result = session.execute(update(ProcessedLot).where(
                    ProcessedLot.id.in_(ids), ProcessedLot.auction_status == "closed",
                    ProcessedLot.is_archived.is_(False),
                ).values(is_archived=True,
                         archived_at=func.coalesce(ProcessedLot.archived_at, now),
                         closed_at=func.coalesce(ProcessedLot.closed_at, now)))
                updated += result.rowcount
            if updated != len(items):
                raise RuntimeError("Updated count differs from locked preview cohort")
            if not rollback_only:
                session.commit()
        session.rollback()
        return {"apply": apply, "committed": apply and not rollback_only,
                "preview_sha256": digest, "selected": len(items), "updated": updated,
                "changed_fields": ["is_archived", "archived_at", "closed_at"],
                "deleted_lots": 0, "items": items}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha")
    parser.add_argument("--rollback-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(apply=args.apply, expected_sha=args.expected_sha,
                             rollback_only=args.rollback_only), default=str))
