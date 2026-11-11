from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from sqlalchemy import select

from bankrotai.db import GeoFailure, ProcessedLot, utc_now
from bankrotai.geo import apply_lot_geo_result, validate_geocoding_result
from bankrotai.services.geo_backfill import geo_input_hash
from bankrotai.services.quality import resolve_geo_failure


_CADASTRAL = re.compile(r"^\d{2}:\d{1,2}:\d{5,9}:\d{1,12}$")
_MAX_EXACT_IDS = 100


def _bounded_ids(values: Iterable[int]) -> list[int]:
    ids = list(dict.fromkeys(int(value) for value in values))
    if not 1 <= len(ids) <= _MAX_EXACT_IDS:
        raise ValueError(f"stale cadastral recovery is bounded to 1..{_MAX_EXACT_IDS} exact lot ids")
    if any(value <= 0 for value in ids):
        raise ValueError("lot ids must be positive")
    return ids


def recover_stale_cadastral_no_match(
    session: Any,
    *,
    lot_ids: Iterable[int],
    live_probe: Callable[[str], tuple[bool, str]],
    apply: bool = False,
) -> dict[str, Any]:
    """Requeue only exact historical no-match rows re-proven by live cadastre.

    The live probe must independently prove the exact same cadastral identity
    with usable coordinates.
    """
    ids = _bounded_ids(lot_ids)
    rows = session.execute(
        select(ProcessedLot, GeoFailure)
        .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(
            ProcessedLot.id.in_(ids),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.auction_status.in_(("active", "scheduled")),
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
            ProcessedLot.review_status.is_(None),
            GeoFailure.status == "deferred_no_match",
        )
    ).all()
    by_id = {int(lot.id): (lot, failure) for lot, failure in rows}

    accepted: list[tuple[ProcessedLot, GeoFailure]] = []
    rejected: list[dict[str, Any]] = []
    for lot_id in ids:
        pair = by_id.get(lot_id)
        if pair is None:
            rejected.append({"lot_id": lot_id, "reason": "not_eligible_deferred_no_match"})
            continue
        lot, failure = pair
        cadastral = re.sub(r"\s+", "", str(lot.cadastral_number or ""))
        if not _CADASTRAL.fullmatch(cadastral):
            rejected.append({"lot_id": lot_id, "reason": "no_valid_cadastral"})
            continue
        try:
            proven, reason = live_probe(cadastral)
        except Exception as exc:
            rejected.append({
                "lot_id": lot_id,
                "reason": "live_probe_operational_failure",
                "error_type": type(exc).__name__,
            })
            continue
        if not proven:
            rejected.append({"lot_id": lot_id, "reason": reason or "live_exact_match_missing"})
            continue
        accepted.append((lot, failure))

    result: dict[str, Any] = {
        "selected": len(ids),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "lot_ids": [int(lot.id) for lot, _failure in accepted],
        "rejections": rejected,
        "updated": 0,
    }
    if not apply:
        return result

    session.expire_all()
    now = utc_now()
    for lot, failure in accepted:
        if (
            lot.current_geo_lat is not None
            or lot.current_geo_lon is not None
            or lot.is_archived
            or lot.review_status is not None
            or failure.status != "deferred_no_match"
        ):
            continue
        lot.needs_geo_check = True
        lot.geo_input_hash = None
        lot.last_update = now
        failure.status = "queued"
        failure.attempt_count = 0
        failure.next_retry_at = now
        failure.resolved_at = None
        result["updated"] += 1
    return result



def recover_stale_cadastral_live_results(
    session: Any,
    *,
    lot_ids: Iterable[int],
    live_result_probe: Callable[[str], tuple[Any | None, str]],
    apply: bool = False,
) -> dict[str, Any]:
    """Validate and persist exact live cadastral results without a second provider call.

    This is the write path for bounded stale NSPD recovery. The probe result is
    independently checked for exact cadastral identity and coordinates, then
    passed through the normal GEO validator and normal snapshot persistence.
    """
    ids = _bounded_ids(lot_ids)
    rows = session.execute(
        select(ProcessedLot, GeoFailure)
        .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(
            ProcessedLot.id.in_(ids),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.auction_status.in_(("active", "scheduled")),
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
            ProcessedLot.review_status.is_(None),
            GeoFailure.status == "deferred_no_match",
        )
    ).all()
    by_id = {int(lot.id): (lot, failure) for lot, failure in rows}

    proven: list[tuple[int, str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for lot_id in ids:
        pair = by_id.get(lot_id)
        if pair is None:
            rejected.append({"lot_id": lot_id, "reason": "not_eligible_deferred_no_match"})
            continue
        lot, _failure = pair
        cadastral = re.sub(r"\s+", "", str(lot.cadastral_number or ""))
        if not _CADASTRAL.fullmatch(cadastral):
            rejected.append({"lot_id": lot_id, "reason": "no_valid_cadastral"})
            continue
        try:
            candidate, probe_reason = live_result_probe(cadastral)
        except Exception as exc:
            rejected.append({
                "lot_id": lot_id,
                "reason": "live_probe_operational_failure",
                "error_type": type(exc).__name__,
            })
            continue
        if candidate is None:
            rejected.append({
                "lot_id": lot_id,
                "reason": probe_reason or "live_exact_match_missing",
            })
            continue

        observed = re.sub(r"\s+", "", str(getattr(candidate, "cadastral_number", "") or ""))
        if observed != cadastral:
            rejected.append({"lot_id": lot_id, "reason": "live_nspd_identity_mismatch"})
            continue
        if getattr(candidate, "lat", None) is None or getattr(candidate, "lon", None) is None:
            rejected.append({"lot_id": lot_id, "reason": "live_nspd_no_coordinates"})
            continue

        valid, validation_reason = validate_geocoding_result(
            candidate,
            cadastral_number=cadastral,
            address=lot.address,
            region_name=lot.region_name,
            region_code=lot.region_code,
        )
        if not valid:
            rejected.append({
                "lot_id": lot_id,
                "reason": f"validation:{validation_reason}",
            })
            continue
        proven.append((lot_id, cadastral, candidate))

    result: dict[str, Any] = {
        "selected": len(ids),
        "accepted": len(proven),
        "rejected": len(rejected),
        "lot_ids": [lot_id for lot_id, _cadastral, _candidate in proven],
        "rejections": rejected,
        "updated": 0,
    }
    if not apply:
        return result

    # Network work above may take several seconds. Refresh all rows before
    # changing production state so a concurrent ingestion update fails closed.
    session.expire_all()
    for lot_id, cadastral, candidate in proven:
        lot = session.get(ProcessedLot, lot_id)
        failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
        if (
            lot is None
            or failure is None
            or lot.current_geo_lat is not None
            or lot.current_geo_lon is not None
            or lot.is_archived
            or lot.duplicate_of_id is not None
            or lot.review_status is not None
            or lot.auction_status not in ("active", "scheduled")
            or failure.status != "deferred_no_match"
        ):
            continue

        current_cadastral = re.sub(r"\s+", "", str(lot.cadastral_number or ""))
        if current_cadastral != cadastral:
            continue

        valid_now, reason_now = validate_geocoding_result(
            candidate,
            cadastral_number=current_cadastral,
            address=lot.address,
            region_name=lot.region_name,
            region_code=lot.region_code,
        )
        if not valid_now:
            result["rejections"].append({
                "lot_id": lot_id,
                "reason": f"validation_after_refresh:{reason_now}",
            })
            continue
        if not apply_lot_geo_result(session, lot, candidate):
            result["rejections"].append({
                "lot_id": lot_id,
                "reason": "apply_lot_geo_result_rejected",
            })
            continue

        lot.geo_input_hash = geo_input_hash(lot)
        resolve_geo_failure(session, lot_id)
        result["updated"] += 1

    result["rejected"] = len(result["rejections"])
    return result
