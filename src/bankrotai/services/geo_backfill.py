from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import exists, or_, select

from bankrotai.core import utc_now
from bankrotai.db import GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import apply_lot_geo_result, resolve_lot_geo
from bankrotai.services.quality import record_geo_failure, resolve_geo_failure


def geocode_pending_lots(
    session_factory: Callable[[], Any],
    *,
    limit: int = 250,
) -> dict[str, int]:
    """Geocode a bounded production batch without holding a DB transaction during the whole run."""
    batch_limit = max(1, min(limit, 1000))
    now = utc_now()
    with session_factory() as session:
        lot_ids = list(session.scalars(
            select(ProcessedLot.id)
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(
                ProcessedLot.duplicate_of_id.is_(None),
                ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
                or_(
                    ProcessedLot.cadastral_number.isnot(None),
                    ProcessedLot.address.isnot(None),
                ),
                or_(
                    GeoFailure.id.is_(None),
                    GeoFailure.next_retry_at.is_(None),
                    GeoFailure.next_retry_at <= now,
                ),
            )
            .order_by(ProcessedLot.needs_geo_check, ProcessedLot.last_update.desc())
            .limit(batch_limit)
        ).all())

    result = {"queued": len(lot_ids), "geocoded": 0, "failed": 0}
    for lot_id in lot_ids:
        try:
            with session_factory() as session:
                lot = session.get(ProcessedLot, lot_id)
                if lot is None:
                    continue
                value = resolve_lot_geo(
                    lot.cadastral_number,
                    lot.address,
                    title=lot.title,
                    region_name=lot.region_name,
                )
                if apply_lot_geo_result(session, lot, value):
                    resolve_geo_failure(session, lot_id)
                    result["geocoded"] += 1
                else:
                    record_geo_failure(
                        session,
                        lot_id,
                        "Coordinates were not found by the scheduled geocoder",
                        retry_after_seconds=21_600,
                    )
                    result["failed"] += 1
        except Exception as exc:
            with session_factory() as session:
                record_geo_failure(
                    session,
                    lot_id,
                    str(exc),
                    retry_after_seconds=21_600,
                )
            result["failed"] += 1
    return result
