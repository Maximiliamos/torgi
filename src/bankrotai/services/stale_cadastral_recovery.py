from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from sqlalchemy import select

from bankrotai.db import GeoFailure, ProcessedLot, utc_now


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

    result = {
        "selected": len(ids),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "lot_ids": [int(lot.id) for lot, _failure in accepted],
        "rejections": rejected,
        "updated": 0,
    }
    if not apply:
        return result

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
