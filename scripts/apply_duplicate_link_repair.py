"""Bounded publication-link policy repair, with preview/rollback/apply guards."""
import hashlib
import json
import sys
from datetime import timedelta

from sqlalchemy import select, text

from bankrotai.db import LotNote, LotSyncRun, ProcessedLot, SessionLocal, utc_now
from bankrotai.services.legacy_duplicate_repair import repair_duplicate_links


def digest(session):
    reviews = [tuple(r) for r in session.execute(select(
        ProcessedLot.id, ProcessedLot.review_status, ProcessedLot.rating,
    ).where(ProcessedLot.review_status.isnot(None)).order_by(ProcessedLot.id))]
    notes = [tuple(r) for r in session.execute(select(
        LotNote.id, LotNote.lot_id, LotNote.user_id, LotNote.content,
    ).order_by(LotNote.id))]
    return hashlib.sha256(json.dumps([reviews, notes], default=str).encode()).hexdigest()


def main():
    payload = json.load(sys.stdin)
    expected = payload.get("expected")
    apply = bool(payload.get("apply"))
    with SessionLocal() as session:
        if apply:
            session.execute(text("SET LOCAL lock_timeout='10s'"))
            active = list(session.scalars(select(LotSyncRun).where(LotSyncRun.status.in_(("queued", "running")))))
            if (len(active) != 1 or active[0].id != payload.get("maintenance_run_id")
                    or active[0].trigger_type != "source_repair" or not active[0].lease_expires_at
                    or active[0].lease_expires_at < utc_now().replace(tzinfo=None)):
                raise RuntimeError("A current exact maintenance lease is required")
            if not expected:
                raise ValueError("Preview is required")
        else:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        before = digest(session)
        result = repair_duplicate_links(session, lot_ids=payload["lot_ids"], apply=apply,
            expected_states={int(i["lot_id"]): i["expected_state"] for i in expected["items"]} if expected else None,
            expected_selection_sha256=expected["selection_sha256"] if expected else None)
        if before != digest(session):
            raise RuntimeError("User reviews or notes changed; transaction must roll back")
        committed = apply and not payload.get("rollback_only")
        if apply:
            run = active[0]
            run.heartbeat_at = utc_now()
            run.lease_expires_at = utc_now() + timedelta(minutes=10)
            run.result_json = {**(run.result_json or {}),
                "duplicate_links_detached": int((run.result_json or {}).get("duplicate_links_detached", 0)) + result["repairable"]}
            session.flush()
        if committed:
            session.commit()
        else:
            session.rollback()
    print(json.dumps({**result, "committed": committed, "reviews_and_notes_preserved": True,
                      "reviews_notes_digest": before, "network_requests": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
