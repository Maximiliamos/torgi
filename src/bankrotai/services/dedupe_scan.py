"""Bounded database scans used by cross-source reconciliation."""

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import ProcessedLot


def iter_primary_processed_lots(
    session: Session, *, batch_size: int = 1000,
) -> Iterator[ProcessedLot]:
    """Yield non-duplicate rows without one statement over the wide table."""
    last_id = 0
    while True:
        rows = session.scalars(
            select(ProcessedLot).where(
                ProcessedLot.duplicate_of_id.is_(None), ProcessedLot.id > last_id,
            ).order_by(ProcessedLot.id).limit(batch_size)
        ).all()
        if not rows:
            return
        last_id = rows[-1].id
        yield from rows
