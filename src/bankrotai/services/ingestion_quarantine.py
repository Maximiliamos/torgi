"""Shared quarantine checks for normalized source records before persistence."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from bankrotai.services.ingestion_models import SourceSyncResult

MAX_QUARANTINED_RECORDS_PER_PAGE = 3
MAX_QUARANTINED_RECORDS_PER_SOURCE = 20


def normalized_lot_rejection_reason(lot: Any, *, expected_source: str) -> str | None:
    """Return a safe rejection code for malformed normalized records."""
    external_id = getattr(lot, "external_id", None)
    if not isinstance(external_id, str) or not external_id.strip():
        return "empty_external_id"
    title = getattr(lot, "title", None)
    if not isinstance(title, str) or not title.strip():
        return "empty_title"
    source_system = getattr(lot, "source_system", None)
    if not isinstance(source_system, str) or source_system != expected_source:
        return "source_system_mismatch"
    return None


def quarantine_sample(lot: Any, *, reason: str) -> dict[str, str | None]:
    """Return non-sensitive evidence for one rejected normalized record."""
    external_id = getattr(lot, "external_id", None)
    fingerprint_payload = {
        "external_id": external_id,
        "source_system": getattr(lot, "source_system", None),
        "title": getattr(lot, "title", None),
        "lot_url": getattr(lot, "lot_url", None),
    }
    serialized = json.dumps(
        fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str,
    )
    return {
        "external_id": str(external_id) if external_id not in (None, "") else None,
        "reason": reason,
        "record_sha256": sha256(serialized.encode("utf-8")).hexdigest(),
        "recovery_failure": None,
    }


def partition_normalized_lots(
    result: SourceSyncResult,
    lots: list[Any],
) -> list[Any]:
    """Quarantine isolated malformed normalized rows before any persistence."""
    accepted: list[Any] = []
    page_quarantined = 0
    for lot in lots:
        reason = normalized_lot_rejection_reason(
            lot, expected_source=result.source_system,
        )
        if reason is None:
            accepted.append(lot)
            continue
        page_quarantined += 1
        result.quarantined_records += 1
        result.items_rejected += 1
        result.quarantine_reasons[reason] = result.quarantine_reasons.get(reason, 0) + 1
        if len(result.quarantine_samples) < 20:
            result.quarantine_samples.append(quarantine_sample(lot, reason=reason))
        if page_quarantined > MAX_QUARANTINED_RECORDS_PER_PAGE:
            raise RuntimeError(
                f"{result.source_system} page has systemic malformed normalized records; "
                f"quarantined={page_quarantined}"
            )
    return accepted


def enforce_source_quarantine_limit(result: SourceSyncResult) -> None:
    if result.quarantined_records > MAX_QUARANTINED_RECORDS_PER_SOURCE:
        raise RuntimeError(
            f"{result.source_system} has systemic malformed normalized records; "
            f"quarantined={result.quarantined_records}"
        )
