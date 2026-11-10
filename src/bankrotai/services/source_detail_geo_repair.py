from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from bankrotai.db import CanonicalLot, GeoFailure, ProcessedLot, SourceLot, utc_now
from bankrotai.domain import NormalizedLot
from bankrotai.logic import normalize_region_code


_MAX_DETAIL_REPAIRS = 20


def _cadastral_region(value: str | None) -> str | None:
    text = str(value or "").strip()
    head = text.split(":", 1)[0] if ":" in text else ""
    return normalize_region_code(head) if head.isdigit() else None


def _bounded_pairs(
    details: Iterable[tuple[int, NormalizedLot]],
) -> list[tuple[int, NormalizedLot]]:
    pairs = [(int(lot_id), detail) for lot_id, detail in details]
    if not pairs:
        raise ValueError("At least one exact detail repair is required")
    if len(pairs) > _MAX_DETAIL_REPAIRS:
        raise ValueError(f"Source-detail GEO repair is bounded to {_MAX_DETAIL_REPAIRS} exact lots")
    ids = [lot_id for lot_id, _detail in pairs]
    if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Processed lot ids must be unique positive integers")
    return pairs


def source_detail_geo_repair_plan(
    session: Any,
    *,
    details: Iterable[tuple[int, NormalizedLot]],
) -> dict[str, Any]:
    """Validate exact source detail against persisted identity without writes."""
    pairs = _bounded_pairs(details)
    ids = [lot_id for lot_id, _detail in pairs]
    rows = {
        int(row.id): row
        for row in session.scalars(
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
    }
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for lot_id, detail in pairs:
        current = rows.get(lot_id)
        reason = None
        if current is None:
            reason = "persisted_lot_not_eligible"
        elif str(current.source_system or "") != str(detail.source_system or ""):
            reason = "source_system_mismatch"
        elif str(current.external_id or "") != str(detail.external_id or ""):
            reason = "external_id_mismatch"
        elif not detail.address and not detail.cadastral_number:
            reason = "detail_has_no_geo_input"
        else:
            current_region = normalize_region_code(str(current.region_code or ""))
            detail_cad_region = _cadastral_region(detail.cadastral_number)
            if current_region and detail_cad_region and current_region != detail_cad_region:
                reason = "cadastral_region_mismatch"

        if reason:
            rejected.append({"lot_id": lot_id, "reason": reason})
            continue
        assert current is not None
        accepted.append({
            "lot_id": lot_id,
            "fill_address": not bool((current.address or "").strip()) and bool(detail.address),
            "fill_cadastral": not bool((current.cadastral_number or "").strip())
            and bool(detail.cadastral_number),
            "_detail": detail,
        })

    return {
        "selected": len(pairs),
        "eligible": len(accepted),
        "rejected": len(rejected),
        "lot_ids": [item["lot_id"] for item in accepted],
        "rejections": rejected,
        "_accepted": accepted,
    }


def repair_source_details_for_geo(
    session: Any,
    *,
    details: Iterable[tuple[int, NormalizedLot]],
    apply: bool = False,
) -> dict[str, Any]:
    """Fill missing GEO inputs from exact verified source details only."""
    plan = source_detail_geo_repair_plan(session, details=details)
    accepted = list(plan.pop("_accepted"))
    result = {
        **plan,
        "updated": 0,
        "addresses_filled": 0,
        "cadastres_filled": 0,
        "geo_requeued": 0,
    }
    if not apply or not accepted:
        return result

    session.expire_all()
    now = utc_now()
    ids = [int(item["lot_id"]) for item in accepted]
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

    for item in accepted:
        lot_id = int(item["lot_id"])
        detail: NormalizedLot = item["_detail"]
        current = processed[lot_id]
        # Revalidate immutable identity in the write transaction.
        if (
            current.review_status is not None
            or current.source_system != detail.source_system
            or current.external_id != detail.external_id
        ):
            continue

        changed = False
        if item["fill_address"] and not (current.address or "").strip() and detail.address:
            current.address = detail.address
            result["addresses_filled"] += 1
            changed = True
        if (
            item["fill_cadastral"]
            and not (current.cadastral_number or "").strip()
            and detail.cadastral_number
        ):
            current.cadastral_number = detail.cadastral_number
            if detail.cadastral_number not in (current.cadastral_numbers or []):
                current.cadastral_numbers = [
                    *(current.cadastral_numbers or []),
                    detail.cadastral_number,
                ]
            result["cadastres_filled"] += 1
            changed = True
        if not changed:
            continue

        current.needs_geo_check = True
        current.geo_input_hash = None
        current.last_update = now

        source = source_rows.get(lot_id)
        if (
            source is not None
            and source.source_system == detail.source_system
            and source.external_id == detail.external_id
        ):
            if not (source.address or "").strip() and detail.address:
                source.address = detail.address
            if not (source.cadastral_number or "").strip() and detail.cadastral_number:
                source.cadastral_number = detail.cadastral_number
            previous_raw = source.raw_data if isinstance(source.raw_data, dict) else {}
            detail_raw = detail.raw_data if isinstance(detail.raw_data, dict) else {}
            source.raw_data = {**previous_raw, **detail_raw}
            canonical = session.get(CanonicalLot, source.canonical_lot_id)
            if canonical is not None:
                if not (canonical.address or "").strip() and detail.address:
                    canonical.address = detail.address
                if not (canonical.cadastral_number or "").strip() and detail.cadastral_number:
                    canonical.cadastral_number = detail.cadastral_number
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
