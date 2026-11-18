"""Fail-closed exclusions for source identities explicitly removed by an operator."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import AuditLog


REMOVAL_ACTIONS = frozenset({"remove_reviewed_reported_lot"})


def excluded_source_identities(session: Session) -> set[tuple[str, str]]:
    """Return exact source identities previously removed by an explicit operator action.

    AuditLog is intentionally retained independently from ProcessedLot, so this
    remains available after physical removal of the selected projection.
    """
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action.in_(REMOVAL_ACTIONS))
    ).all()
    identities: set[tuple[str, str]] = set()
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        source = str(metadata.get("source_system") or "").strip()
        external_id = str(metadata.get("external_id") or "").strip()
        if source and external_id:
            identities.add((source, external_id))
    return identities


def filter_excluded_lots(session: Session, lots: Iterable[object]) -> tuple[list[object], int]:
    """Drop only exact identities with an explicit historical removal record."""
    rows = list(lots)
    if not rows:
        return [], 0
    excluded = excluded_source_identities(session)
    kept: list[object] = []
    removed = 0
    for lot in rows:
        identity = (
            str(getattr(lot, "source_system", "") or "").strip(),
            str(getattr(lot, "external_id", "") or "").strip(),
        )
        if identity in excluded:
            removed += 1
            continue
        kept.append(lot)
    return kept, removed
