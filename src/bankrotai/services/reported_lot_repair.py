"""Exact-ID source corrections with a read-only plan and protected user reviews."""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Any

from sqlalchemy import select, func

from bankrotai.db import CanonicalLot, GeoFailure, LotIssueReport, ProcessedLot, SourceLot, utc_now
from bankrotai.domain import NormalizedLot
from bankrotai.logic import _sync_source_lot, apply_lot_status, log_action
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot


SOURCE_FIELDS = (
    "title", "description", "category", "address", "cadastral_number", "start_price",
    "current_price", "auction_status", "application_start_at", "application_deadline",
    "auction_at", "auction_timezone", "deposit_amount", "deposit_percent", "auction_type",
    "public_offer_schedule", "next_interval_price", "next_price_reduction_at",
)
DATE_FIELDS = {field.name for field in fields(NormalizedLot) if "datetime" in str(field.type)}
PROCESSED_FIELDS = frozenset(column.name for column in ProcessedLot.__table__.columns)
MONEY_FIELDS = {"start_price", "current_price", "deposit_amount", "next_interval_price"}


def decode_lot(payload: dict) -> NormalizedLot:
    values = dict(payload)
    for key in DATE_FIELDS:
        if isinstance(values.get(key), str):
            values[key] = datetime.fromisoformat(values[key])
    values["auction_timezone"] = "Europe/Moscow"
    return NormalizedLot(**values)


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _state(record, source) -> str:
    data = [[column.name, _plain(getattr(row, column.name))] for row in (record, source) if row is not None
            for column in row.__table__.columns]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def repair_reported_lots(
    session, *, evidence: list[dict], apply: bool = False,
    allow_reviewed_source_fields: bool = False, expected_states: dict[int, str] | None = None,
    recheck_geo_ids: set[int] | None = None, last_report: int = 44,
) -> dict[str, Any]:
    """Never insert lots, merge identities, clear reviews, or reconcile a source."""
    if not evidence or len(evidence) > 100:
        raise ValueError("Repair requires 1..100 exact reported lots")
    ids = [int(item["lot_id"]) for item in evidence]
    if len(ids) != len(set(ids)) or any(value <= 0 for value in ids):
        raise ValueError("Repair identities must be unique positive lot IDs")
    reported_ids = set(session.scalars(select(LotIssueReport.lot_id).where(LotIssueReport.id <= last_report)))
    if not set(ids) <= reported_ids:
        raise ValueError("Repair includes a lot outside the exact report allowlist")
    items = []
    for item in evidence:
        lot_id = int(item["lot_id"])
        record_query = select(ProcessedLot).where(ProcessedLot.id == lot_id)
        source_query = select(SourceLot).where(SourceLot.processed_lot_id == lot_id)
        record = session.scalar(record_query.with_for_update() if apply else record_query)
        source = session.scalar(source_query.with_for_update() if apply else source_query)
        if record is None:
            raise ValueError(f"Missing exact projection/source link: {lot_id}")
        if item.get("error") or "normalized" not in item:
            items.append({"lot_id": lot_id, "blocked": "source_evidence_unavailable"})
            continue
        lot = decode_lot(item["normalized"])
        identity = (lot.source_system, lot.external_id)
        if identity != (record.source_system, record.external_id) or (source is not None and identity != (source.source_system, source.external_id)):
            raise ValueError(f"Exact source identity mismatch: {lot_id}")
        if source is None:
            identity_query = select(SourceLot).where(
                SourceLot.source_system == lot.source_system, SourceLot.external_id == lot.external_id,
            )
            source = session.scalar(identity_query.with_for_update() if apply else identity_query)
            if source is not None and source.processed_lot_id not in {None, lot_id}:
                raise ValueError(f"Source identity is linked to another projection: {lot_id}")
        state = _state(record, source)
        if apply and (expected_states is None or expected_states.get(lot_id) != state):
            raise ValueError(f"Repair preview is stale: {lot_id}")
        observed = set(lot.raw_data.get("repair_observed_fields", SOURCE_FIELDS))
        observed &= set(SOURCE_FIELDS)
        changes = {}
        for name in SOURCE_FIELDS:
            if name not in observed:
                continue
            current = getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None)
            value = getattr(lot, name)
            if _plain(current) != _plain(value):
                changes[name] = {"before": _plain(current), "after": _plain(value)}
        protected = record.review_status is not None and not allow_reviewed_source_fields
        entry = {"lot_id": lot_id, "source_system": lot.source_system, "external_id": lot.external_id,
                 "review_status": record.review_status, "protected": protected, "expected_state": state,
                 "changes": changes, "exclude_from_property_map": not is_sale_real_estate_lot(lot)}
        entry["restore_source_provenance"] = source is None or source.processed_lot_id is None
        items.append(entry)
        if not apply or protected:
            continue
        # Unobserved fields retain their existing source values; public/auth-limited pages
        # do not erase historical dates or prices merely because they hide them.
        for name in SOURCE_FIELDS:
            if name not in observed:
                setattr(lot, name, getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None))
        canonical = session.get(CanonicalLot, source.canonical_lot_id) if source is not None else None
        links = session.scalar(select(func.count()).select_from(SourceLot).where(SourceLot.canonical_lot_id == canonical.id)) if canonical is not None else 0
        if canonical is not None and links and links > 1 and lot.cadastral_number and canonical.cadastral_number != lot.cadastral_number:
            # A previously poisoned source row must not overwrite another asset's canonical.
            if canonical.legacy_processed_lot_id == lot_id:
                canonical.legacy_processed_lot_id = None
                session.flush()
            canonical = CanonicalLot(canonical_key=f"source-repair:{lot.source_system}:{lot.external_id}",
                                     legacy_processed_lot_id=lot_id, title=lot.title,
                                     category=lot.category, address=lot.address, cadastral_number=lot.cadastral_number)
            session.add(canonical)
            session.flush()
            assert source is not None
            source.canonical_lot_id = canonical.id
        for name in observed & PROCESSED_FIELDS:
            if name == "auction_status":
                apply_lot_status(session, record, lot.auction_status, "exact_report_repair")
            else:
                value = getattr(lot, name)
                setattr(record, name, Decimal(str(value)) if name in MONEY_FIELDS and value is not None else value)
        if lot.raw_data.get("cadastral_numbers"):
            record.cadastral_numbers = list(lot.raw_data["cadastral_numbers"])
        if source is not None and source.processed_lot_id is None:
            source.processed_lot_id = lot_id
        source = _sync_source_lot(session, record, lot, canonical_hint=canonical, isolate_source=True)
        canonical = session.get(CanonicalLot, source.canonical_lot_id)
        assert canonical is not None
        canonical.category = lot.category
        record.last_update = utc_now()
        if entry["exclude_from_property_map"]:
            record.is_archived = source.is_archived = True
            source.is_active = False
            source.archive_reason = "exact_report_non_property_sale"
            source.archived_at = source.archived_at or utc_now()
        if any(name in changes for name in ("address", "cadastral_number", "title", "description")) or lot_id in (recheck_geo_ids or set()):
            record.current_geo_lat = record.current_geo_lon = None
            record.current_geo_source = record.current_geo_confidence = None
            record.current_geo_observed_at = record.geo_input_hash = None
            record.needs_geo_check = lot.auction_status in {"active", "scheduled"} and not entry["exclude_from_property_map"]
            failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
            if failure is not None:
                session.delete(failure)
        log_action(session, "operator", "exact_report_source_repair", "lot", str(lot_id),
                   {"changes": changes, "review_status_preserved": record.review_status,
                    "external_id": lot.external_id, "source_system": lot.source_system})
    return {"apply": apply, "selected": len(ids), "protected": sum(bool(item.get("protected")) for item in items),
            "blocked": sum(bool(item.get("blocked")) for item in items), "items": items,
            "reconcile_missing": False, "inserted_lots": 0}
