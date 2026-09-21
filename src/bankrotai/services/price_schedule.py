from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from bankrotai.db import LotPriceEvent, ProcessedLot, SourceLot
from bankrotai.logic import _to_datetime, _to_decimal
from bankrotai.services.trusted_time import trusted_utc_now


def _period_value(period: dict[str, Any], *keys: str) -> Any:
    return next((period[key] for key in keys if period.get(key) is not None), None)


def active_schedule_period(
    schedule: object,
    *,
    now: datetime,
) -> tuple[Decimal, datetime | None, Decimal | None] | None:
    """Return active price, next boundary and next price from a normalized schedule."""
    if not isinstance(schedule, list):
        return None
    periods: list[tuple[datetime, datetime, Decimal]] = []
    for raw in schedule:
        if not isinstance(raw, dict):
            continue
        starts_at = _to_datetime(_period_value(raw, "starts_at", "start_at", "start"))
        ends_at = _to_datetime(_period_value(raw, "ends_at", "end_at", "end"))
        price = _to_decimal(_period_value(raw, "price", "amount", "current_price"))
        if starts_at is None or ends_at is None or price is None or ends_at <= starts_at:
            continue
        periods.append((starts_at, ends_at, price))
    periods.sort(key=lambda item: item[0])
    for index, (starts_at, ends_at, price) in enumerate(periods):
        if starts_at <= now < ends_at:
            next_price = periods[index + 1][2] if index + 1 < len(periods) else None
            return price, ends_at, next_price
    return None


def recalculate_public_offer_prices(
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    observed_at = now or trusted_utc_now().replace(tzinfo=None)
    checked = changed = 0
    with session_factory() as session:
        rows = session.scalars(
            select(SourceLot).where(
                SourceLot.is_active.is_(True),
                SourceLot.is_archived.is_(False),
                SourceLot.public_offer_schedule.isnot(None),
            )
        ).all()
        for row in rows:
            checked += 1
            active = active_schedule_period(row.public_offer_schedule, now=observed_at)
            if active is None:
                continue
            current_price, next_at, next_price = active
            row.next_price_reduction_at = next_at
            row.next_interval_price = next_price
            if row.current_price == current_price:
                continue
            row.current_price = current_price
            if row.processed_lot_id is not None:
                processed = session.get(ProcessedLot, row.processed_lot_id)
                if processed is not None:
                    processed.current_price = current_price
                    processed.last_update = observed_at
                    session.add(LotPriceEvent(
                        lot_id=processed.id,
                        source=row.source_system,
                        price_kind="current",
                        amount=current_price,
                        observed_at=observed_at,
                        metadata_json={
                            "reason": "public_offer_period_transition",
                            "source_lot_id": row.id,
                        },
                    ))
            changed += 1
        session.commit()
    return {"checked": checked, "changed": changed}
