"""Persist GEO results only while their source inputs remain unchanged."""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from bankrotai.db import ProcessedLot


def final_geo_result_allowed(lot, result):
    """Cached and fallback results must satisfy the current lot before saving."""
    if (result is None or result.lat is None or result.lon is None
            or getattr(result, "source", None) == "manual"):
        return True
    from bankrotai.geo import validate_geocoding_result

    cadastre = lot.cadastral_number
    if result.cadastral_number and result.cadastral_number in (lot.cadastral_numbers or []):
        cadastre = result.cadastral_number
    valid, reason = validate_geocoding_result(result, cadastral_number=cadastre,
        address=lot.address, region_name=lot.region_name, region_code=lot.region_code)
    if not valid:
        lot.needs_geo_check = True
        result.attempts = list(result.attempts or []) + [
            {"source": result.source, "valid": False, "reason": reason}]
        result.error = "validation:" + reason
    return valid


def _same_inputs(lot, item, expected_key):
    from bankrotai.services.geo_backfill import GeoWorkItem, _work_key

    current = GeoWorkItem(
        lot.id, lot.cadastral_number, lot.cadastral_numbers, lot.address,
        lot.title, lot.description, lot.region_name, lot.region_code,
    )
    return (
        lot.id == item.lot_id and not lot.is_archived
        and lot.duplicate_of_id is None and _work_key(current) == expected_key
    )


def save_geo_chunk(session_factory: Callable[[], Any], chunk, resolved):
    from bankrotai.services.geo_backfill import _save_geo_item

    with session_factory() as session:
        lots = {lot.id: lot for lot in session.scalars(
            select(ProcessedLot)
            .where(ProcessedLot.id.in_([item.lot_id for _, item in chunk]))
            .order_by(ProcessedLot.id).with_for_update()
        ).all()}
        outcomes = []
        for key, item in chunk:
            lot = lots.get(item.lot_id)
            if lot is None:
                continue
            outcomes.append(
                _save_geo_item(session, lot, resolved.get(key))
                if _same_inputs(lot, item, key) else (False, "geo_input_changed")
            )
        session.commit()
        return outcomes


def save_geo_item_isolated(session_factory: Callable[[], Any], key, item, resolved):
    from bankrotai.services.geo_backfill import _record_scheduled_failure, _save_geo_item

    try:
        with session_factory() as session:
            lot = session.scalar(select(ProcessedLot).where(
                ProcessedLot.id == item.lot_id,
            ).with_for_update())
            if lot is None:
                return None
            if not _same_inputs(lot, item, key):
                return False, "geo_input_changed"
            outcome = _save_geo_item(session, lot, resolved.get(key))
            session.commit()
            return outcome
    except Exception as exc:
        with session_factory() as session:
            _record_scheduled_failure(session, item.lot_id, str(exc))
            session.commit()
        return False, f"persistence:{exc.__class__.__name__}"[:160]
