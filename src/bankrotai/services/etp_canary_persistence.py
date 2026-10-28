"""Additional cross-source collision guard for manual ETP canary imports.

The current general deduplicator uses cadastral overlap. That is not by
itself proof of an identical *sale* (the same property may be reauctioned).
Canary imports must not merge a suspicious external source record silently.
"""
from __future__ import annotations

from sqlalchemy import select

from bankrotai.db import ProcessedLot
from bankrotai.logic import log_action, persist_lot


class CanaryDuplicateReviewRequired(ValueError):
    """Another platform already has a property with the same cadastral ID."""


def persist_canary_lot(session, normalized, actor_id: str):
    cadastral = (normalized.cadastral_number or "").strip()
    if not cadastral:
        raise ValueError("Canary import requires a verified cadastral number")
    duplicate = session.scalar(
        select(ProcessedLot.id).where(
            ProcessedLot.cadastral_number == cadastral,
            ProcessedLot.source_system != normalized.source_system,
            ProcessedLot.is_archived.is_(False),
        ).limit(1)
    )
    if duplicate is not None:
        raise CanaryDuplicateReviewRequired(
            "Another source has the same cadastral number; manual review required"
        )
    record = persist_lot(session, normalized)
    log_action(
        session, actor_id, "import_verified_etp_canary", "lot", str(record.id),
        {"source_system": normalized.source_system,
         "external_id": normalized.external_id,
         "reconcile_missing": False, "manual_canary": True},
    )
    return record
