"""Additional cross-source collision guard for manual ETP canary imports.

The current general deduplicator uses cadastral overlap. That is not by
itself proof of an identical *sale* (the same property may be reauctioned).
Canary imports must not merge a suspicious external source record silently.
"""
from __future__ import annotations

from sqlalchemy import select

from bankrotai.db import ProcessedLot, SourceLot
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
    # Recheck source-only provenance at the actual write boundary. The
    # preflight is SELECT-only and a new source row may appear afterwards.
    if session.scalar(
        select(SourceLot.id).where(
            SourceLot.cadastral_number == cadastral,
            SourceLot.source_system != normalized.source_system,
            SourceLot.is_archived.is_(False),
        ).limit(1)
    ) is not None:
        raise CanaryDuplicateReviewRequired(
            "Another source provenance has the same cadastral number; manual review required"
        )
    # This manual first import must never silently overwrite an existing
    # source identity that appeared between preview and transaction.
    if session.scalar(
        select(ProcessedLot.id).where(
            ProcessedLot.source_system == normalized.source_system,
            ProcessedLot.external_id == normalized.external_id,
        ).limit(1)
    ) is not None or session.scalar(
        select(SourceLot.id).where(
            SourceLot.source_system == normalized.source_system,
            SourceLot.external_id == normalized.external_id,
        ).limit(1)
    ) is not None:
        raise CanaryDuplicateReviewRequired(
            "ETP canary source identity is already present; manual review required"
        )
    # Cadastral overlap, same price or the same address does not prove the
    # identity of the auction sale on another trading platform.
    record = persist_lot(session, normalized, isolate_source=True)
    log_action(
        session, actor_id, "import_verified_etp_canary", "lot", str(record.id),
        {"source_system": normalized.source_system,
         "external_id": normalized.external_id,
         "reconcile_missing": False, "manual_canary": True},
    )
    return record
