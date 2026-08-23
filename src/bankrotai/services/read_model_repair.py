from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from bankrotai.db import CanonicalLot, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.services.batch_persistence import _processed_values


def _normalized_from_source(row: SourceLot) -> NormalizedLot:
    raw = dict(row.raw_data or {})
    return NormalizedLot(
        external_id=row.external_id,
        source=str(raw.get("source") or row.source_system),
        source_system=row.source_system,
        title=row.title or row.description or row.external_id,
        description=row.description or "",
        category=row.category or "other",
        region_slug=row.region_code,
        region_name=row.region_name,
        address=row.address,
        cadastral_number=row.cadastral_number,
        vin=None,
        area=None,
        start_price=float(row.start_price) if row.start_price is not None else None,
        current_price=float(row.current_price) if row.current_price is not None else None,
        auction_status=row.source_status or ("active" if row.is_active else "closed"),
        lot_url=row.lot_url,
        source_url=row.source_url,
        detail_level="search",
        raw_data=raw,
        published_at=row.published_at,
        platform_name=row.platform_name,
        platform_code=row.platform_code,
        procedure_number=row.procedure_number,
        notice_number=row.notice_number,
        efresb_message_number=row.efresb_message_number,
        debtor_name=row.debtor_name,
        organizer_name=row.organizer_name,
        auction_manager_name=row.auction_manager_name,
        bankruptcy_case_number=row.bankruptcy_case_number,
        deposit_amount=float(row.deposit_amount) if row.deposit_amount is not None else None,
        deposit_percent=row.deposit_percent,
        deposit_payment_details=row.deposit_payment_details,
        deposit_deadline=row.deposit_deadline,
        application_deadline=row.application_deadline,
        auction_at=row.auction_at,
        auction_step_amount=float(row.auction_step_amount) if row.auction_step_amount is not None else None,
        auction_step_percent=row.auction_step_percent,
        auction_type=row.auction_type,
        public_offer_schedule=row.public_offer_schedule,
        next_interval_price=float(row.next_interval_price) if row.next_interval_price is not None else None,
        next_price_reduction_at=row.next_price_reduction_at,
        document_completeness=row.document_completeness,
        inspection_procedure=row.inspection_procedure,
        organizer_contact=row.organizer_contact,
    )


def repair_missing_processed_links(
    session: Session,
    *,
    limit: int = 1000,
    source_systems: tuple[str, ...] = ("torgi.gov.ru", "torgi-russia.ru"),
) -> dict[str, int]:
    """Restore the map read model from trusted SourceLot rows without source I/O."""
    rows = list(session.scalars(
        select(SourceLot)
        .where(SourceLot.processed_lot_id.is_(None), SourceLot.source_system.in_(source_systems))
        .order_by(SourceLot.id)
        .limit(max(1, min(limit, 5000)))
    ).all())
    if not rows:
        return {"selected": 0, "repaired": 0}

    dialect = session.get_bind().dialect.name
    insert_factory: Any = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert_factory is None:
        raise RuntimeError(f"Read-model repair is unsupported for dialect {dialect}")

    normalized = [_normalized_from_source(row) for row in rows]
    values = [_processed_values(lot) for lot in normalized]
    statement = insert_factory(ProcessedLot).values(values)
    excluded = statement.excluded
    immutable = {"source_system", "external_id", "id", "created_at", "review_status"}
    session.execute(statement.on_conflict_do_update(
        index_elements=["source_system", "external_id"],
        set_={
            column.name: getattr(excluded, column.name)
            for column in ProcessedLot.__table__.columns
            if column.name not in immutable and column.name in values[0]
        },
        where=ProcessedLot.review_status.is_(None),
    ))
    ids_by_identity = {
        (item.source_system, item.external_id): item.id
        for item in session.scalars(select(ProcessedLot).where(
            ProcessedLot.source_system.in_(source_systems),
            ProcessedLot.external_id.in_([row.external_id for row in rows]),
        )).all()
    }
    for row in rows:
        processed_id = ids_by_identity[(row.source_system, row.external_id)]
        row.processed_lot_id = processed_id
        canonical = session.get(CanonicalLot, row.canonical_lot_id)
        if canonical is not None and canonical.legacy_processed_lot_id is None:
            canonical.legacy_processed_lot_id = processed_id
    session.commit()
    return {"selected": len(rows), "repaired": len(rows)}
