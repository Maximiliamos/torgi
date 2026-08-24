from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select

from bankrotai.core import utc_now
from bankrotai.db import GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import apply_lot_geo_result, resolve_lot_geo
from bankrotai.services.quality import record_geo_failure, resolve_geo_failure


_BASE_RETRY_SECONDS = 21_600
_MAX_RETRY_SECONDS = 604_800
_MAX_ATTEMPTS = 8


def geocoding_statistics(session: Any) -> dict[str, int]:
    latest_ids = (
        select(LotGeoSnapshot.lot_id, func.max(LotGeoSnapshot.id).label("geo_id"))
        .join(ProcessedLot, ProcessedLot.id == LotGeoSnapshot.lot_id)
        .where(ProcessedLot.is_archived.is_(False))
        .group_by(LotGeoSnapshot.lot_id)
        .subquery()
    )
    rows = session.execute(
        select(LotGeoSnapshot.geo_source, LotGeoSnapshot.geo_confidence, func.count())
        .join(latest_ids, LotGeoSnapshot.id == latest_ids.c.geo_id)
        .group_by(LotGeoSnapshot.geo_source, LotGeoSnapshot.geo_confidence)
    ).all()
    active = int(session.scalar(select(func.count()).where(ProcessedLot.is_archived.is_(False))) or 0)
    with_coordinates = sum(int(count) for _source, _confidence, count in rows)
    result = {
        "active_lots": active,
        "with_coordinates": with_coordinates,
        "without_coordinates": max(0, active - with_coordinates),
        "ik12": 0,
        "nspd": 0,
        "address": 0,
        "low_confidence": 0,
        "geocoding_failed": int(
            session.scalar(select(func.count()).where(GeoFailure.status.not_in(("resolved",)))) or 0
        ),
    }
    for source, confidence, count in rows:
        key = (
            "ik12" if source == "ik12_cadastral"
            else "nspd" if source == "nspd"
            else "address" if source == "nominatim"
            else None
        )
        if key:
            result[key] += int(count)
        if confidence in {"low", "none", "unknown"}:
            result["low_confidence"] += int(count)
    return result


def _record_scheduled_failure(session: Any, lot_id: int, error: str) -> None:
    failure = record_geo_failure(
        session,
        lot_id,
        error,
        retry_after_seconds=_BASE_RETRY_SECONDS,
    )
    if failure.attempt_count >= _MAX_ATTEMPTS:
        failure.status = "terminal"
        failure.next_retry_at = None
        return
    delay = min(
        _BASE_RETRY_SECONDS * (2 ** (failure.attempt_count - 1)),
        _MAX_RETRY_SECONDS,
    )
    failure.next_retry_at = utc_now() + timedelta(seconds=delay)


def geocode_pending_lots(
    session_factory: Callable[[], Any],
    *,
    limit: int = 250,
    re_geocode_existing: bool = False,
) -> dict[str, int]:
    """Geocode a bounded production batch without holding a DB transaction during the whole run."""
    batch_limit = max(1, min(limit, 1000))
    now = utc_now()
    with session_factory() as session:
        latest_geo_id = (
            select(func.max(LotGeoSnapshot.id))
            .where(LotGeoSnapshot.lot_id == ProcessedLot.id)
            .correlate(ProcessedLot)
            .scalar_subquery()
        )
        pending_filter = (
            or_(
                ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id),
                ProcessedLot.needs_geo_check.is_(True),
                exists().where(
                    (LotGeoSnapshot.id == latest_geo_id)
                    & or_(
                        LotGeoSnapshot.geo_confidence.in_(("low", "none", "unknown")),
                        LotGeoSnapshot.geo_source.not_in(("ik12_cadastral", "nspd", "nominatim")),
                    )
                ),
            )
            if re_geocode_existing
            else ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id)
        )
        lot_ids = list(
            session.scalars(
                select(ProcessedLot.id)
                .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
                .where(
                    ProcessedLot.duplicate_of_id.is_(None),
                    ProcessedLot.is_archived.is_(False),
                    or_(
                        ProcessedLot.cadastral_number.isnot(None),
                        ProcessedLot.address.isnot(None),
                    ),
                    pending_filter,
                    or_(
                        GeoFailure.id.is_(None),
                        GeoFailure.next_retry_at.is_(None),
                        GeoFailure.next_retry_at <= now,
                    ),
                    or_(GeoFailure.status.is_(None), GeoFailure.status != "terminal"),
                )
                .order_by(ProcessedLot.needs_geo_check.desc(), ProcessedLot.last_update.desc())
                .limit(batch_limit)
            ).all()
        )

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
                    description=lot.description,
                    region_name=lot.region_name,
                )
                if apply_lot_geo_result(session, lot, value):
                    resolve_geo_failure(session, lot_id)
                    result["geocoded"] += 1
                else:
                    _record_scheduled_failure(
                        session,
                        lot_id,
                        "Coordinates were not found by the scheduled geocoder",
                    )
                    result["failed"] += 1
        except Exception as exc:
            with session_factory() as session:
                _record_scheduled_failure(session, lot_id, str(exc))
            result["failed"] += 1
    return result
