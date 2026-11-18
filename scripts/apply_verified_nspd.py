"""Bounded NSPD evidence replay; preview, rollback trial or guarded apply.

Read one JSON payload from stdin. Operator wrapper verifies the restorable
backup before applying. This script never contacts external providers.
"""
import hashlib
import json
import sys

from sqlalchemy import select, text

from bankrotai.db import LotNote, LotSyncRun, ProcessedLot, SessionLocal
from bankrotai.services.nspd_evidence import nspd_result_from_receipt
from bankrotai.services.stale_cadastral_recovery import recover_stale_cadastral_live_results
from bankrotai.services.verified_current_nspd import replay_current_nspd


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
    receipts = payload["receipts"]
    mode = payload.get("mode", "stale_no_match")
    if mode not in {"stale_no_match", "current_missing_geo", "current_address_precision"}:
        raise ValueError("Unknown NSPD operator mode")
    ids = [int(lot_id) for r in receipts for lot_id in r["lot_ids"]]
    if not receipts or not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids):
        raise ValueError("Expected 1..100 distinct exact lot IDs")
    proof_sha = hashlib.sha256(json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload.get("apply") and payload.get("expected_proof_sha256") != proof_sha:
        raise ValueError("NSPD evidence changed after preview")
    results = {r["cadastral_number"]: nspd_result_from_receipt(r) for r in receipts}
    with SessionLocal() as session:
        if payload.get("apply"):
            session.execute(text("SET LOCAL lock_timeout = '10s'"))
            active = list(session.scalars(select(LotSyncRun.id).where(LotSyncRun.status.in_(("queued", "running")))))
            if active:
                raise RuntimeError("Active ingestion/repair; defer bounded NSPD replay")
        else:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        before = digest(session)
        result = replay_current_nspd(session,receipts=receipts,apply=bool(payload.get("apply")),
            expected=payload.get("expected_current_plan"),
            replace_address_precision=mode == "current_address_precision") if mode in {"current_missing_geo", "current_address_precision"} else recover_stale_cadastral_live_results(
            session, lot_ids=ids,
            live_result_probe=lambda cadastre: (results.get(cadastre), "primary_receipt_identity_missing"),
            apply=bool(payload.get("apply")),
        )
        preserved = before == digest(session)
        if not preserved:
            session.rollback()
            raise RuntimeError("Reviews/notes changed during NSPD transaction")
        committed = bool(payload.get("apply") and not payload.get("rollback_only"))
        if committed:
            session.commit()
        else:
            session.rollback()
    print(json.dumps({**result, "mode": mode, "evidence_sha256": proof_sha, "committed": committed,
                      "reviews_and_notes_preserved": preserved, "deleted_lots": 0,
                      "network_requests": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
