from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from bankrotai.db import CanonicalLot, MapDataset, MapTile, ProcessedLot, SourceLot
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


def audit_read_model_links(session: Session, *, limit: int = 10_000) -> dict[str, Any]:
    """Classify missing read-model links without changing application data.

    A matching source identity alone is not enough to authorize repair.  The
    candidate must also be unused by another SourceLot and compatible with the
    source's canonical record.  The returned records are intentionally verbose
    so an operator can retain the report as rollout evidence.
    """
    current = session.scalar(select(MapDataset).where(MapDataset.is_current.is_(True)))
    visible_ids: set[int] = set()
    if current is not None:
        for payload in session.scalars(select(MapTile.payload_json).where(
            MapTile.dataset_id == current.id,
            MapTile.z == 12,
        )):
            for feature in (payload or {}).get("features", []):
                if feature.get("kind") == "lot" and isinstance(feature.get("id"), int):
                    visible_ids.add(feature["id"])

    sources = list(session.scalars(
        select(SourceLot)
        .where(SourceLot.processed_lot_id.is_(None))
        .order_by(SourceLot.id)
        .limit(max(1, min(limit, 100_000)))
    ).all())
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for source in sources:
        candidates = list(session.scalars(select(ProcessedLot).where(
            ProcessedLot.source_system == source.source_system,
            ProcessedLot.external_id == source.external_id,
        )).all())
        candidate = candidates[0] if len(candidates) == 1 else None
        used_elsewhere = bool(candidate and session.scalar(select(exists().where(
            SourceLot.processed_lot_id == candidate.id,
        ))))
        canonical = session.get(CanonicalLot, source.canonical_lot_id)
        canonical_conflict = bool(
            candidate
            and canonical
            and canonical.legacy_processed_lot_id not in {None, candidate.id}
        )
        if len(candidates) > 1:
            category, reason, severity = "identity_conflict", "multiple_processed_identity_matches", "critical"
        elif candidate and used_elsewhere:
            category, reason, severity = "identity_conflict", "processed_candidate_already_linked", "high"
        elif candidate and canonical_conflict:
            category, reason, severity = "canonical_conflict", "canonical_points_to_other_processed_lot", "high"
        elif candidate:
            category, reason, severity = "recoverable_legacy_link", "unique_conflict_free_identity_match", "medium"
        elif source.is_active and not source.is_archived:
            category, reason, severity = "active_missing_read_model", "no_processed_identity_match", "critical"
        else:
            category, reason, severity = "archived_legacy_orphan", "archived_source_without_read_model", "low"
        counts[category] = counts.get(category, 0) + 1
        records.append({
            "record_type": "source_without_processed_link",
            "source_system": source.source_system,
            "external_id": source.external_id,
            "source_lot_id": source.id,
            "status": source.source_status,
            "is_active": source.is_active,
            "is_archived": source.is_archived,
            "first_seen_at": source.first_seen_at.isoformat() if source.first_seen_at else None,
            "last_seen_at": source.last_seen_at.isoformat() if source.last_seen_at else None,
            "canonical_lot_id": source.canonical_lot_id,
            "processed_lot_id": None,
            "candidate_processed_lot_id": candidate.id if candidate else None,
            "reason": reason,
            "category": category,
            "repairable": category == "recoverable_legacy_link",
            "in_current_map_dataset": bool(candidate and candidate.id in visible_ids),
            "severity": severity,
        })

    processed_without_source = list(session.scalars(
        select(ProcessedLot)
        .where(~exists().where(SourceLot.processed_lot_id == ProcessedLot.id))
        .order_by(ProcessedLot.id)
        .limit(max(1, min(limit, 100_000)))
    ).all())
    return {
        "dry_run": True,
        "current_dataset_version": current.version if current else None,
        "source_without_processed_link": len(sources),
        "processed_without_source_link": len(processed_without_source),
        "counts_by_category": counts,
        "records": records,
        "processed_without_source": [
            {
                "record_type": "processed_without_source_link",
                "source_system": row.source_system,
                "external_id": row.external_id,
                "processed_lot_id": row.id,
                "is_archived": row.is_archived,
                "status": row.auction_status,
                "in_current_map_dataset": row.id in visible_ids,
                "severity": "high" if not row.is_archived else "medium",
            }
            for row in processed_without_source
        ],
    }
