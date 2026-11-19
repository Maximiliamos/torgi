"""Link proven spelling aliases without deleting or moving user records."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import select

from bankrotai.db import LotNote, ProcessedLot, SourceLot
from bankrotai.logic import log_action
from bankrotai.services.mets_identity_aliases import mets_native_identity
from bankrotai.services.reported_lot_repair import _state
from bankrotai.services.source_lot_exclusions import excluded_ids


def _proof(source, native, now):
    if source is None:
        return None
    raw = source.raw_data or {}
    proof = (raw.get("verified_source_fields") or {}).get("title") or {}
    sha = proof.get("sha256")
    try:
        checked = datetime.fromisoformat(proof["checked_at_utc"])
    except (TypeError, ValueError, KeyError):
        return None
    if (raw.get("primary_provider_external_id") != native or raw.get("stored_id_spelling_verified") is not True
            or proof.get("value") != source.title or not source.title
            or checked.tzinfo is None or not timedelta(0) <= now-checked <= timedelta(hours=24)
            or not isinstance(sha, str) or len(sha) != 64
            or any(c not in "0123456789abcdef" for c in sha.lower())):
        return None
    return sha, checked


def repair_mets_alias_links(session, *, lot_ids, apply=False, expected_states=None,
                            expected_selection_sha256=None):
    ids = [int(value) for value in lot_ids]
    if not ids or len(ids) > 1000 or len(set(ids)) != len(ids) or min(ids) < 1:
        raise ValueError("Expected 1..1000 distinct exact projection IDs")
    selection_sha = hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
    if apply and (expected_states is None or selection_sha != expected_selection_sha256):
        raise ValueError("Exact preview is required")
    query = select(ProcessedLot).where(ProcessedLot.id.in_(ids)).order_by(ProcessedLot.id)
    rows = list(session.scalars(query.with_for_update() if apply else query))
    if len(rows) != len(ids):
        raise ValueError("A selected projection disappeared")
    by_native = defaultdict(list)
    for row in rows:
        native = mets_native_identity(row.source_system, row.external_id)
        if native is None:
            raise ValueError("Selection contains a non-M-ETS trade/lot identity")
        by_native[native].append(row)
    if any(len(pair) != 2 for pair in by_native.values()):
        raise ValueError("Every exact pair must contain both stored spellings")
    source_query = select(SourceLot).where(SourceLot.source_system == "m-ets.ru",
                                           SourceLot.processed_lot_id.in_(ids)).order_by(SourceLot.id)
    sources = {(s.processed_lot_id, s.external_id): s for s in session.scalars(
        source_query.with_for_update() if apply else source_query)}
    note_query = select(LotNote).where(LotNote.lot_id.in_(ids)).order_by(LotNote.id)
    notes = defaultdict(list)
    for note in session.scalars(note_query.with_for_update() if apply else note_query):
        notes[note.lot_id].append(note)
    child_query = select(ProcessedLot).where(ProcessedLot.duplicate_of_id.in_(ids)).order_by(ProcessedLot.id)
    children = defaultdict(list)
    for child in session.scalars(child_query.with_for_update() if apply else child_query):
        children[child.duplicate_of_id].append(child)
    excluded = excluded_ids(session, "m-ets.ru", [r.external_id for r in rows])
    now = datetime.now(timezone.utc)
    items = []
    selected_pairs = []
    for native, pair in sorted(by_native.items()):
        protected = [p for p in pair if p.review_status is not None or notes[p.id]]
        primary = protected[0] if len(protected) == 1 else next(p for p in pair if p.external_id == native)
        secondary = next(p for p in pair if p.id != primary.id)
        source_pair = [sources.get((p.id, p.external_id)) for p in pair]
        state = hashlib.sha256(json.dumps([
            [_state(p, None) for p in pair], [_state(s, None) for s in source_pair],
            [_state(n, None) for p in pair for n in notes[p.id]],
            [_state(c, None) for p in pair for c in children[p.id]],
            sorted(excluded & {p.external_id for p in pair}),
        ], sort_keys=True).encode()).hexdigest()
        if apply and expected_states.get(secondary.id) != state:
            raise ValueError(f"M-ETS alias preview is stale: {secondary.id}")
        proof_pair = [_proof(s, native, now) for s in source_pair]
        blocked = None
        if len(protected) > 1:
            blocked = "both_records_have_user_state"
        elif any(p.external_id in excluded for p in pair):
            blocked = "user_excluded_publication"
        elif any(proof is None for proof in proof_pair) or proof_pair[0] != proof_pair[1]:
            blocked = "fresh_matching_primary_identity_proof_required"
        elif primary.duplicate_of_id is not None or secondary.duplicate_of_id not in {None, primary.id}:
            blocked = "existing_non_alias_link"
        elif children[secondary.id]:
            blocked = "secondary_has_dependent_records"
        link = blocked is None and secondary.duplicate_of_id != primary.id
        items.append({"lot_id": secondary.id, "primary_id": primary.id, "native_external_id": native,
                      "link": link, "blocked": blocked, "expected_state": state})
        selected_pairs.append((primary, secondary))
    if apply:
        for (primary, secondary), item in zip(selected_pairs, items):
            if not item["link"]:
                continue
            secondary.duplicate_of_id = primary.id
            log_action(session, "operator", "mets_exact_alias_link_repair", "lot", str(secondary.id),
                       {"primary_id": primary.id, "native_external_id": item["native_external_id"],
                        "source_fact_changes": 0, "user_state_transfers": 0, "deleted_lots": 0})
        session.flush()
    changed = sum(item["link"] for item in items)
    return {"selected": len(ids), "repairable": changed, "unchanged": len(items)-changed,
            "blocked": sum(item["blocked"] is not None for item in items), "items": items,
            "selection_sha256": selection_sha, "deleted_lots": 0, "inserted_lots": 0,
            "source_fact_changes": 0, "canonical_changes": 0, "reconcile_missing": False}
