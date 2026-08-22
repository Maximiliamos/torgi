from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from bankrotai.db import CanonicalLot, LotStatusHistory, ProcessedLot, SourceLot, utc_now
from bankrotai.domain import NormalizedLot
from bankrotai.logic import (
    _canonical_key,
    _raw_value,
    _to_datetime,
    _to_decimal,
    _to_float,
    normalize_region_code,
    normalize_status,
)


def _processed_values(lot: NormalizedLot, existing: ProcessedLot | None = None) -> dict[str, Any]:
    auction_status = lot.auction_status
    if normalize_status(auction_status) == "unknown" and existing is not None:
        auction_status = existing.auction_status
    return {
        "external_id": lot.external_id,
        "source": lot.source,
        "source_system": lot.source_system,
        "title": lot.title,
        "description": lot.description,
        "category": lot.category,
        "region_slug": lot.region_slug,
        "region_name": lot.region_name,
        "address": lot.address,
        "cadastral_number": lot.cadastral_number,
        "cadastral_numbers": lot.raw_data.get("cadastral_numbers"),
        "vin": lot.vin,
        "start_price": _to_decimal(lot.start_price),
        "current_price": _to_decimal(lot.current_price),
        "auction_status": auction_status,
        "lot_url": lot.lot_url,
        "source_url": lot.source_url,
        "area": lot.area,
        "detail_level": lot.detail_level,
        "published_at": lot.published_at,
        "object_name": lot.object_name,
        "property_type": lot.property_type,
        "total_area_gba": lot.total_area_gba,
        "gla": lot.gla,
        "land_area": lot.land_area,
        "floors": lot.floors,
        "year_built": lot.year_built,
        "occupancy_rate": lot.occupancy_rate,
        "anchor_tenants": lot.anchor_tenants,
        "monthly_fixed_rent": lot.monthly_fixed_rent,
        "monthly_variable_rent": lot.monthly_variable_rent,
        "monthly_other_income": lot.monthly_other_income,
        "monthly_opex": lot.monthly_opex,
        "noi_annual": lot.noi_annual,
        "legal_status": lot.legal_status,
        "encumbrances": lot.encumbrances,
        "land_risk_flag": lot.land_risk_flag,
        "technical_condition": lot.technical_condition,
        "power_kw": lot.power_kw,
        "parking_spaces": lot.parking_spaces,
        "last_update": utc_now(),
    }


def _source_values(
    lot: NormalizedLot,
    *,
    run_id: str,
    canonical_lot_id: int,
    processed_lot_id: int,
    existing: SourceLot | None = None,
) -> dict[str, Any]:
    raw = lot.raw_data or {}
    now = utc_now()
    status = normalize_status(lot.auction_status)
    is_active = existing.is_active if existing is not None else True
    is_archived = existing.is_archived if existing is not None else False
    archived_at = existing.archived_at if existing is not None else None
    archive_reason = existing.archive_reason if existing is not None else None
    if status in {"active", "scheduled"}:
        is_active, is_archived, archived_at, archive_reason = True, False, None, None
    elif status == "closed":
        is_active, is_archived, archived_at, archive_reason = False, True, now, "source_status"
    return {
        "canonical_lot_id": canonical_lot_id,
        "processed_lot_id": processed_lot_id,
        "source_system": lot.source_system,
        "external_id": lot.external_id,
        "source_url": lot.source_url or lot.lot_url,
        "lot_url": lot.lot_url,
        "etp_url": _raw_value(raw, "etp_url"),
        "title": lot.title,
        "description": lot.description,
        "category": lot.category,
        "region_code": normalize_region_code(
            str(raw.get("region_code") or lot.region_name or lot.region_slug or "")
        ),
        "region_name": lot.region_name,
        "address": lot.address,
        "cadastral_number": lot.cadastral_number,
        "start_price": _to_decimal(lot.start_price),
        "current_price": _to_decimal(lot.current_price),
        "source_status": lot.auction_status,
        "platform_name": lot.platform_name or _raw_value(raw, "etp", "platform_name", "trade_place"),
        "platform_code": lot.platform_code or _raw_value(raw, "etp_code", "platform_code"),
        "procedure_number": lot.procedure_number or _raw_value(raw, "procedure_number", "trade_number"),
        "notice_number": lot.notice_number or _raw_value(raw, "notice_number", "noticeNumber"),
        "efresb_message_number": lot.efresb_message_number or _raw_value(
            raw, "efresb_message_number", "fedresurs_message_number"
        ),
        "debtor_name": lot.debtor_name or _raw_value(raw, "debtor", "debtor_name"),
        "organizer_name": lot.organizer_name or _raw_value(raw, "organizer", "organizer_name"),
        "auction_manager_name": lot.auction_manager_name or _raw_value(raw, "auction_manager", "arbitration_manager"),
        "bankruptcy_case_number": lot.bankruptcy_case_number or _raw_value(
            raw, "bankruptcy_case_number", "case_number"
        ),
        "deposit_amount": _to_decimal(
            lot.deposit_amount if lot.deposit_amount is not None else _to_float(_raw_value(raw, "deposit", "deposit_amount"))
        ),
        "deposit_percent": lot.deposit_percent or _to_float(_raw_value(raw, "deposit_percent")),
        "deposit_payment_details": lot.deposit_payment_details or _raw_value(
            raw, "deposit_payment_details", "deposit_requisites"
        ),
        "deposit_deadline": lot.deposit_deadline or _to_datetime(_raw_value(raw, "deposit_deadline")),
        "application_deadline": lot.application_deadline or _to_datetime(
            _raw_value(raw, "bidd_end_time", "application_deadline")
        ),
        "auction_at": lot.auction_at or _to_datetime(_raw_value(raw, "auction_start_date", "auction_at")),
        "published_at": lot.published_at,
        "source_updated_at": _to_datetime(_raw_value(raw, "updated_at", "source_updated_at", "last_update")),
        "auction_step_amount": _to_decimal(
            lot.auction_step_amount
            if lot.auction_step_amount is not None
            else _to_float(_raw_value(raw, "auction_step", "auction_step_amount"))
        ),
        "auction_step_percent": lot.auction_step_percent or _to_float(_raw_value(raw, "auction_step_percent")),
        "auction_type": lot.auction_type or _raw_value(raw, "trade_type", "auction_type"),
        "public_offer_schedule": lot.public_offer_schedule or _raw_value(raw, "public_offer_schedule"),
        "next_interval_price": _to_decimal(
            lot.next_interval_price
            if lot.next_interval_price is not None
            else _to_float(_raw_value(raw, "next_interval_price"))
        ),
        "next_price_reduction_at": lot.next_price_reduction_at or _to_datetime(
            _raw_value(raw, "next_price_reduction_at")
        ),
        "document_completeness": lot.document_completeness or _raw_value(raw, "document_completeness"),
        "inspection_procedure": lot.inspection_procedure or _raw_value(raw, "inspection_procedure"),
        "organizer_contact": lot.organizer_contact or _raw_value(raw, "organizer_contact"),
        "raw_data": raw,
        "last_seen_at": now,
        "last_sync_run_id": run_id,
        "is_active": is_active,
        "is_archived": is_archived,
        "archived_at": archived_at,
        "archive_reason": archive_reason,
        "missing_successful_runs": 0,
    }


def persist_changed_lots_batch(
    session: Session,
    lots: list[NormalizedLot],
    run_id: str,
    *,
    batch_size: int = 500,
    existing_sources: dict[str, SourceLot] | None = None,
) -> None:
    """Set-based exact-identity persistence; fuzzy reconciliation remains a later phase."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    for offset in range(0, len(lots), batch_size):
        _persist_changed_lots_chunk(
            session,
            lots[offset : offset + batch_size],
            run_id,
            existing_sources=existing_sources or {},
        )


def _persist_changed_lots_chunk(
    session: Session,
    lots: list[NormalizedLot],
    run_id: str,
    *,
    existing_sources: dict[str, SourceLot],
) -> None:
    if not lots:
        return
    dialect = session.get_bind().dialect.name
    insert_factory = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert_factory is None:
        raise RuntimeError(f"Batch source persistence is unsupported for dialect {dialect}")

    canonical_values = {
        _canonical_key(lot): {
            "canonical_key": _canonical_key(lot),
            "title": lot.title,
            "category": lot.category,
            "region_code": normalize_region_code(
                str((lot.raw_data or {}).get("region_code") or lot.region_name or lot.region_slug or "")
            ),
            "address": lot.address,
            "cadastral_number": lot.cadastral_number,
            "area": lot.area,
        }
        for lot in lots
    }
    canonical_statement = insert_factory(CanonicalLot).values(list(canonical_values.values()))
    canonical_excluded = canonical_statement.excluded
    session.execute(canonical_statement.on_conflict_do_update(
        index_elements=["canonical_key"],
        set_={
            "title": canonical_excluded.title,
            "category": canonical_excluded.category,
            "region_code": canonical_excluded.region_code,
            "address": canonical_excluded.address,
            "cadastral_number": canonical_excluded.cadastral_number,
            "area": canonical_excluded.area,
            "updated_at": utc_now(),
        },
    ))
    keys = set(canonical_values)
    canonicals = {
        row.canonical_key: row
        for row in session.scalars(select(CanonicalLot).where(CanonicalLot.canonical_key.in_(keys))).all()
    }

    external_ids = [lot.external_id for lot in lots]
    existing_processed = {
        row.external_id: row
        for row in session.scalars(select(ProcessedLot).where(
            ProcessedLot.source_system == lots[0].source_system,
            ProcessedLot.external_id.in_(external_ids),
        )).all()
    }
    old_status_by_id = {
        external_id: row.auction_status
        for external_id, row in existing_processed.items()
    }
    protected_ids = {
        external_id
        for external_id, row in existing_processed.items()
        if row.review_status is not None
    }
    processed_values = [_processed_values(lot, existing_processed.get(lot.external_id)) for lot in lots]
    processed_statement = insert_factory(ProcessedLot).values(processed_values)
    processed_excluded = processed_statement.excluded
    immutable_processed = {"source_system", "external_id", "id", "created_at", "review_status"}
    session.execute(processed_statement.on_conflict_do_update(
        index_elements=["source_system", "external_id"],
        set_={
            column.name: getattr(processed_excluded, column.name)
            for column in ProcessedLot.__table__.columns
            if column.name not in immutable_processed and column.name in processed_values[0]
        },
        where=ProcessedLot.review_status.is_(None),
    ))
    processed = {
        row.external_id: row
        for row in session.scalars(select(ProcessedLot).where(
            ProcessedLot.source_system == lots[0].source_system,
            ProcessedLot.external_id.in_(external_ids),
        )).all()
    }
    history_values = []
    for lot, proposed in zip(lots, processed_values, strict=True):
        old_status = old_status_by_id.get(lot.external_id)
        new_status = old_status if lot.external_id in protected_ids else proposed["auction_status"]
        if new_status and new_status != old_status:
            history_values.append({
                "lot_id": processed[lot.external_id].id,
                "old_status": old_status,
                "new_status": new_status,
                "source": lot.source or "sync",
            })
    if history_values:
        session.execute(insert(LotStatusHistory), history_values)

    values = [
        _source_values(
            lot,
            run_id=run_id,
            canonical_lot_id=canonicals[_canonical_key(lot)].id,
            processed_lot_id=processed[lot.external_id].id,
            existing=existing_sources.get(lot.external_id),
        )
        for lot in lots
    ]
    statement = insert_factory(SourceLot).values(values)
    excluded = statement.excluded
    immutable = {"source_system", "external_id", "first_seen_at", "created_at"}
    update_values = {
        column.name: getattr(excluded, column.name)
        for column in SourceLot.__table__.columns
        if column.name not in immutable and column.name != "id" and column.name in values[0]
    }
    session.execute(statement.on_conflict_do_update(
        index_elements=["source_system", "external_id"],
        set_=update_values,
    ))
