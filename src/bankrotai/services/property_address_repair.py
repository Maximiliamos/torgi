from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from bankrotai.db import CanonicalLot, GeoFailure, ProcessedLot, SourceLot, utc_now
from bankrotai.services.property_address_evidence import extract_asset_property_address


_MAX_EXACT_IDS = 100


def _bounded_ids(lot_ids: Iterable[int]) -> list[int]:
    ids = []
    seen = set()
    for value in lot_ids:
        lot_id = int(value)
        if lot_id <= 0 or lot_id in seen:
            continue
        seen.add(lot_id)
        ids.append(lot_id)
    if not ids:
        raise ValueError("At least one exact lot id is required")
    if len(ids) > _MAX_EXACT_IDS:
        raise ValueError(f"Property-address repair is bounded to {_MAX_EXACT_IDS} exact lot ids")
    return ids


def property_address_repair_plan(session: Any, *, lot_ids: Iterable[int]) -> dict[str, Any]:
    """Plan a bounded historical address repair without modifying persistence.

    Only unmapped, active/scheduled, non-reviewed rows whose stored address is
    empty are eligible. The candidate must come from the same shared
    property-scoped evidence helper used by the source parsers.
    """
    ids = _bounded_ids(lot_ids)
    rows = session.scalars(
        select(ProcessedLot).where(
            ProcessedLot.id.in_(ids),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.auction_status.in_(("active", "scheduled")),
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
            ProcessedLot.review_status.is_(None),
        )
    ).all()
    by_id = {int(row.id): row for row in rows}
    repairable: list[dict[str, Any]] = []
    for lot_id in ids:
        lot = by_id.get(lot_id)
        if lot is None or (lot.address or "").strip():
            continue
        candidate = extract_asset_property_address(
            title=lot.title,
            description=lot.description,
            cadastral_number=lot.cadastral_number,
        )
        if not candidate:
            continue
        repairable.append({
            "lot_id": lot_id,
            "source_system": str(lot.source_system or ""),
            "candidate": candidate,
        })
    return {
        "selected": len(ids),
        "eligible_rows": len(rows),
        "repairable": len(repairable),
        "lot_ids": [item["lot_id"] for item in repairable],
        # Candidate text intentionally stays internal to the process. Operator
        # summaries expose only IDs/source counts, not arbitrary source text.
        "_repairs": repairable,
    }


def repair_property_addresses(
    session: Any,
    *,
    lot_ids: Iterable[int],
    apply: bool = False,
) -> dict[str, Any]:
    """Apply only a bounded exact-ID address repair and release its GEO failure."""
    plan = property_address_repair_plan(session, lot_ids=lot_ids)
    repairs = list(plan.pop("_repairs"))
    result = {**plan, "updated": 0, "geo_requeued": 0}
    if not apply or not repairs:
        return result

    # The read-only plan may spend time validating evidence. Drop ORM snapshots
    # before the write phase so a concurrent GEO/source worker cannot be
    # overwritten by stale in-memory state.
    session.expire_all()
    now = utc_now()
    ids = [int(item["lot_id"]) for item in repairs]
    processed = {
        int(row.id): row
        for row in session.scalars(select(ProcessedLot).where(ProcessedLot.id.in_(ids))).all()
    }
    source_rows = {
        int(row.processed_lot_id): row
        for row in session.scalars(
            select(SourceLot).where(SourceLot.processed_lot_id.in_(ids))
        ).all()
        if row.processed_lot_id is not None
    }
    failures = {
        int(row.lot_id): row
        for row in session.scalars(select(GeoFailure).where(GeoFailure.lot_id.in_(ids))).all()
    }

    for item in repairs:
        lot_id = int(item["lot_id"])
        candidate = str(item["candidate"])
        lot = processed[lot_id]
        # Recheck under the write transaction: never overwrite a value that
        # appeared after the read-only plan.
        if (lot.address or "").strip() or lot.review_status is not None:
            continue
        lot.address = candidate
        lot.needs_geo_check = True
        lot.geo_input_hash = None
        lot.last_update = now

        source = source_rows.get(lot_id)
        if source is not None and not (source.address or "").strip():
            source.address = candidate
            canonical = session.get(CanonicalLot, source.canonical_lot_id)
            if canonical is not None and not (canonical.address or "").strip():
                canonical.address = candidate
                canonical.updated_at = now

        failure = failures.get(lot_id)
        if failure is not None and failure.status != "resolved":
            failure.status = "queued"
            failure.attempt_count = 0
            failure.next_retry_at = now
            failure.resolved_at = None
            result["geo_requeued"] += 1
        result["updated"] += 1

    return result
