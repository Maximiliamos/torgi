from __future__ import annotations

from typing import Any

from sqlalchemy import select

from bankrotai.db import CanonicalLot, ProcessedLot, SourceLot, utc_now
from bankrotai.extractors import extract_address


def _is_better_address(old: str | None, candidate: str | None) -> bool:
    if not candidate:
        return False
    if not old:
        return True
    old_clean = old.strip(" ,.;:-")
    candidate_clean = candidate.strip(" ,.;:-")
    return len(candidate_clean) >= len(old_clean) + 5 and candidate_clean.casefold().startswith(old_clean.casefold())


def repair_bidexpert_addresses(session: Any, *, limit: int = 20_000, apply: bool = False) -> dict[str, int]:
    rows = session.scalars(
        select(SourceLot)
        .where(SourceLot.source_system == "bidexpert.ru")
        .order_by(SourceLot.id)
        .limit(max(1, min(limit, 100_000)))
    ).all()
    result = {"selected": len(rows), "repairable": 0, "updated": 0}
    for source in rows:
        candidate = extract_address(" ".join(filter(None, (source.title, source.description))))
        if not _is_better_address(source.address, candidate):
            continue
        result["repairable"] += 1
        if not apply:
            continue
        old = source.address
        source.address = candidate
        processed = session.get(ProcessedLot, source.processed_lot_id) if source.processed_lot_id else None
        if processed is not None and (not processed.address or processed.address == old):
            processed.address = candidate
            processed.needs_geo_check = True
            processed.last_update = utc_now()
        canonical = session.get(CanonicalLot, source.canonical_lot_id)
        if canonical is not None and (not canonical.address or canonical.address == old):
            canonical.address = candidate
            canonical.updated_at = utc_now()
        result["updated"] += 1
    return result
