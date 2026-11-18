"""Restore publications hidden by unsupported legacy duplicate links.

This changes projection links and the GEO queue flag only. It neither asserts
new external source facts nor transfers user reviews, notes or workflow state.
"""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from bankrotai.db import ProcessedLot, utc_now
from bankrotai.logic import log_action, _processed_lot_cadastral_numbers, _same_processed_cross_source_lot, _ensure_processed_source_lot
from bankrotai.services.duplicate_projection_policy import valid_duplicate_target
from bankrotai.services.reported_lot_repair import _state
from bankrotai.services.dedupe_scan import iter_primary_processed_lots


def _detach(session, record, *, actor):
    previous = record.duplicate_of_id
    record.duplicate_of_id = None
    if (not record.is_archived and record.auction_status in {"active", "scheduled"}
            and (record.current_geo_lat is None or record.current_geo_lon is None)):
        record.needs_geo_check = True
    record.last_update = utc_now()
    log_action(session, actor, "legacy_duplicate_policy_repair", "lot", str(record.id),
               {"previous_parent_id": previous, "new_parent_id": None,
                "reason": "same_sale_identity_not_proven", "source_fact_changes": 0})


def repair_duplicate_links(session, *, lot_ids, apply=False, expected_states=None,
                           expected_selection_sha256=None):
    ids = [int(value) for value in lot_ids]
    if not ids or len(ids) > 1000 or len(ids) != len(set(ids)) or min(ids) < 1:
        raise ValueError("Expected 1..1000 distinct exact projection IDs")
    selection_sha = hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
    if apply and (expected_states is None or expected_selection_sha256 != selection_sha):
        raise ValueError("Exact selection and preview are required")
    query = select(ProcessedLot).where(ProcessedLot.id.in_(ids)).order_by(ProcessedLot.id)
    records = list(session.scalars(query.with_for_update() if apply else query))
    if len(records) != len(ids):
        raise ValueError("A selected projection disappeared")
    parents = {record.duplicate_of_id for record in records if record.duplicate_of_id}
    parent_query = select(ProcessedLot).where(ProcessedLot.id.in_(parents)).order_by(ProcessedLot.id)
    parent_rows = {r.id: r for r in session.scalars(parent_query.with_for_update() if apply else parent_query)}
    items = []
    for record in records:
        parent = parent_rows.get(record.duplicate_of_id)
        state = hashlib.sha256(json.dumps([_state(record, None), _state(parent, None) if parent else None]).encode()).hexdigest()
        if apply and expected_states.get(record.id) != state:
            raise ValueError(f"Duplicate-link preview is stale: {record.id}")
        detach = bool(record.duplicate_of_id and not valid_duplicate_target(record, parent))
        items.append({"lot_id": record.id, "parent_id": record.duplicate_of_id,
                      "detach": detach, "expected_state": state})
    # Validate every row before performing the first update in this batch.
    if apply:
        for record, item in zip(records, items):
            if item["detach"]:
                _detach(session, record, actor="operator")
        session.flush()
    changed = sum(item["detach"] for item in items)
    return {"selected": len(ids), "repairable": changed, "unchanged": len(ids) - changed,
            "selection_sha256": selection_sha, "items": items, "deleted_lots": 0,
            "inserted_lots": 0, "source_fact_changes": 0, "canonical_changes": 0,
            "reconcile_missing": False}


def revalidate_duplicate_links(session, *, batch_size=500):
    """Keep ordinary reconciliation from retaining obsolete suppression links."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("Expected a bounded duplicate scan")
    parent = aliased(ProcessedLot)
    last_id = repaired = 0
    while True:
        rows = session.execute(select(ProcessedLot, parent)
            .outerjoin(parent, parent.id == ProcessedLot.duplicate_of_id)
            .where(ProcessedLot.id > last_id, ProcessedLot.duplicate_of_id.isnot(None))
            .order_by(ProcessedLot.id).limit(batch_size)).all()
        if not rows:
            return repaired
        last_id = rows[-1][0].id
        for record, target in rows:
            if not valid_duplicate_target(record, target):
                _detach(session, record, actor="system")
                repaired += 1
        session.flush()


def reconcile_source_copies(session: Session) -> int:
    """Mark existing cross-source copies and link them to one canonical card."""
    detached = revalidate_duplicate_links(session)
    by_cadastral: dict[str, list[ProcessedLot]] = {}
    by_price: dict[int, list[ProcessedLot]] = {}
    reconciled = 0

    # Retain global candidate indexes, but page the wide 200k+ row table so a
    # single ordered SELECT cannot exceed PostgreSQL statement_timeout.
    for lot in iter_primary_processed_lots(session):
        candidates: list[ProcessedLot] = []
        for cadastral in _processed_lot_cadastral_numbers(lot):
            candidates.extend(by_cadastral.get(cadastral, []))
        price = lot.current_price or lot.start_price
        if price is not None:
            candidates.extend(by_price.get(int(round(float(price))), []))

        primary = next((candidate for candidate in dict.fromkeys(candidates)
                        if _same_processed_cross_source_lot(candidate, lot)), None)
        if primary is not None:
            lot.duplicate_of_id = primary.id
            primary.address = primary.address or lot.address
            primary.cadastral_number = primary.cadastral_number or lot.cadastral_number
            primary.cadastral_numbers = primary.cadastral_numbers or lot.cadastral_numbers
            primary.region_slug = primary.region_slug or lot.region_slug
            primary.region_name = primary.region_name or lot.region_name
            primary.area = primary.area if primary.area is not None else lot.area
            primary.review_status = primary.review_status or lot.review_status
            primary_link = _ensure_processed_source_lot(session, primary)
            duplicate_link = _ensure_processed_source_lot(session, lot)
            duplicate_link.canonical_lot_id = primary_link.canonical_lot_id
            reconciled += 1
            continue

        for cadastral in _processed_lot_cadastral_numbers(lot):
            by_cadastral.setdefault(cadastral, []).append(lot)
        if price is not None:
            by_price.setdefault(int(round(float(price))), []).append(lot)

    detached += revalidate_duplicate_links(session)
    session.info["duplicate_links_detached"] = detached
    session.flush()
    return reconciled
