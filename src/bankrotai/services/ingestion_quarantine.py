"""Shared quarantine checks for normalized source records before persistence."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

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
    source_system = getattr(lot, "source_system", None)
    fingerprint_payload = {
        "external_id": external_id,
        "source_system": source_system,
        "title": getattr(lot, "title", None),
        "lot_url": getattr(lot, "lot_url", None),
    }
    serialized = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return {
        "external_id": str(external_id) if external_id not in (None, "") else None,
        "reason": reason,
        "record_sha256": sha256(serialized.encode("utf-8")).hexdigest(),
        "recovery_failure": None,
    }
