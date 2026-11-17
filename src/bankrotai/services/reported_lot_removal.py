"""Physical removal of an explicitly selected, reviewed report cohort."""
from __future__ import annotations

from sqlalchemy import delete, exists, select, update, or_

from bankrotai.db import Base, CanonicalLot, LotIssueReport, ProcessedLot, SourceLot
from bankrotai.logic import log_action
from bankrotai.services.reported_lot_repair import _state


def _remove_direct_children(session, target: str, ids: list[int], *, preserve: set[str]) -> dict[str, int]:
    counts = {}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in preserve:
            continue
        columns = [fk.parent for fk in table.foreign_keys if fk.target_fullname == target]
        if columns:
            result = session.execute(delete(table).where(or_(*(column.in_(ids) for column in columns))))
            counts[table.name] = result.rowcount
    return counts


def remove_reviewed_reported_lots(
    session, *, lot_ids: list[int], apply: bool = False,
    expected_states: dict[int, str] | None = None, last_report: int = 44,
) -> dict:
    """Preserve historical issue reports and all other lots/canonical members."""
    ids = [int(value) for value in lot_ids]
    if not ids or len(ids) > 100 or len(set(ids)) != len(ids) or any(value <= 0 for value in ids):
        raise ValueError("Removal requires 1..100 unique positive exact lot IDs")
    reported = set(session.scalars(select(LotIssueReport.lot_id).where(LotIssueReport.id <= last_report)))
    if not set(ids) <= reported:
        raise ValueError("Removal includes a lot outside the exact report cohort")
    items = []
    canonical_ids = set()
    source_ids = []
    for lot_id in ids:
        query = select(ProcessedLot).where(ProcessedLot.id == lot_id)
        record = session.scalar(query.with_for_update() if apply else query)
        if record is None or record.review_status not in {"approved", "maybe", "rejected"}:
            raise ValueError(f"Reported lot is missing or no longer reviewed: {lot_id}")
        query = select(SourceLot).where(SourceLot.source_system == record.source_system,
                                        SourceLot.external_id == record.external_id)
        source = session.scalar(query.with_for_update() if apply else query)
        if source is not None and source.processed_lot_id not in {None, lot_id}:
            raise ValueError(f"Source identity belongs to another projection: {lot_id}")
        other_sources = session.scalars(select(SourceLot.id).where(
            SourceLot.processed_lot_id == lot_id,
            (SourceLot.source_system != record.source_system) | (SourceLot.external_id != record.external_id),
        )).all()
        if other_sources:
            raise ValueError(f"Other source identities depend on the selected projection: {lot_id}")
        state = _state(record, source)
        if apply and (expected_states is None or expected_states.get(lot_id) != state):
            raise ValueError(f"Removal preview is stale: {lot_id}")
        if source is not None:
            source_ids.append(source.id)
            canonical_ids.add(source.canonical_lot_id)
        items.append({"lot_id": lot_id, "source_system": record.source_system,
                      "external_id": record.external_id, "review_status": record.review_status,
                      "title": record.title, "source_lot_id": source.id if source else None,
                      "expected_state": state})
    if apply:
        for item in items:
            log_action(session, "operator", "remove_reviewed_reported_lot", "lot", str(item["lot_id"]),
                       {"external_id": item["external_id"], "source_system": item["source_system"],
                        "reason": "explicit_user_request_remove_incorrect_reviewed_lots"})
        # Older production migrations used NO ACTION even where today's model
        # declares CASCADE. Remove only exact descendants explicitly and preserve
        # shared projections/canonicals rather than relying on the DDL generation.
        source_children = _remove_direct_children(session, "source_lots.id", source_ids,
                                                  preserve={"source_lots"})
        processed_children = _remove_direct_children(session, "processed_lots.id", ids,
            preserve={"source_lots", "processed_lots", "canonical_lots"})
        session.execute(update(ProcessedLot).where(ProcessedLot.duplicate_of_id.in_(ids)).values(duplicate_of_id=None))
        session.execute(update(CanonicalLot).where(CanonicalLot.legacy_processed_lot_id.in_(ids)).values(legacy_processed_lot_id=None))
        session.execute(delete(SourceLot).where(SourceLot.id.in_(source_ids)))
        session.execute(delete(ProcessedLot).where(ProcessedLot.id.in_(ids)))
        session.flush()
        session.execute(delete(CanonicalLot).where(
            CanonicalLot.id.in_(canonical_ids), CanonicalLot.legacy_processed_lot_id.is_(None),
            ~exists(select(SourceLot.id).where(SourceLot.canonical_lot_id == CanonicalLot.id)),
        ))
        session.flush()
    return {"apply": apply, "selected": len(ids), "deleted_lots": len(ids) if apply else 0,
            "source_lots": len(source_ids), "items": items, "historical_reports_preserved": True,
            "removed_related_rows": {**source_children, **processed_children} if apply else {},
            "reconcile_missing": False}
