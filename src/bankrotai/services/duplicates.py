from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import CanonicalLot, DuplicateReview, ProcessedLot, SourceLot


def _require_lot(session: Session, lot_id: int) -> ProcessedLot:
    lot = session.get(ProcessedLot, lot_id)
    if lot is None:
        raise ValueError(f"Lot {lot_id} does not exist")
    return lot


def manual_merge_lots(
    session: Session,
    primary_lot_id: int,
    secondary_lot_id: int,
    *,
    reason: str = "",
    user_id: str = "desktop",
) -> DuplicateReview:
    if primary_lot_id == secondary_lot_id:
        raise ValueError("A lot cannot be merged with itself")
    primary = _require_lot(session, primary_lot_id)
    secondary = _require_lot(session, secondary_lot_id)
    if primary.duplicate_of_id is not None:
        primary = _require_lot(session, primary.duplicate_of_id)
    if secondary.id == primary.id:
        raise ValueError("Lots are already merged")
    secondary.duplicate_of_id = primary.id

    primary_source = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == primary.id))
    if primary_source is not None:
        for source in session.scalars(select(SourceLot).where(SourceLot.processed_lot_id == secondary.id)).all():
            source.canonical_lot_id = primary_source.canonical_lot_id

    review = DuplicateReview(
        primary_lot_id=primary.id,
        secondary_lot_id=secondary.id,
        action="merge",
        reason=reason or None,
        user_id=user_id,
    )
    session.add(review)
    session.flush()
    return review


def manual_split_lot(
    session: Session,
    lot_id: int,
    *,
    reason: str = "",
    user_id: str = "desktop",
) -> DuplicateReview:
    lot = _require_lot(session, lot_id)
    if lot.duplicate_of_id is None:
        raise ValueError("Lot is not marked as a duplicate")
    former_primary_id = lot.duplicate_of_id
    lot.duplicate_of_id = None
    own_canonical = session.scalar(select(CanonicalLot).where(CanonicalLot.legacy_processed_lot_id == lot.id))
    if own_canonical is not None:
        for source in session.scalars(select(SourceLot).where(SourceLot.processed_lot_id == lot.id)).all():
            source.canonical_lot_id = own_canonical.id
    review = DuplicateReview(
        primary_lot_id=former_primary_id,
        secondary_lot_id=lot.id,
        action="split",
        reason=reason or None,
        user_id=user_id,
    )
    session.add(review)
    session.flush()
    return review
