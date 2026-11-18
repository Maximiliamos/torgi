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
from bankrotai.regions import REGIONS_BY_CODE
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot


DEFAULT_REPORT_FIELDS = (
    "title", "description", "category", "address", "cadastral_number", "region_slug", "region_name", "detail_level", "start_price", "lot_url", "source_url",
    "current_price", "auction_status", "application_start_at", "application_deadline",
    "auction_at", "auction_timezone", "deposit_amount", "deposit_percent", "auction_type",
    "public_offer_schedule", "next_interval_price", "next_price_reduction_at",
)
SOURCE_FIELDS = DEFAULT_REPORT_FIELDS + (
    "published_at", "auction_step_amount", "auction_step_percent", "notice_number", "procedure_number",
)
DATE_FIELDS = {field.name for field in fields(NormalizedLot) if "datetime" in str(field.type)}
PROCESSED_FIELDS = frozenset(column.name for column in ProcessedLot.__table__.columns)
MONEY_FIELDS = {"start_price", "current_price", "deposit_amount", "next_interval_price", "auction_step_amount"}


def decode_lot(payload: dict) -> NormalizedLot:
    values = dict(payload)
    for key in DATE_FIELDS:
        if isinstance(values.get(key), str):
            values[key] = datetime.fromisoformat(values[key])
    values["auction_timezone"] = "Europe/Moscow"
    cadastral = str(values.get("cadastral_number") or "")
    region = cadastral.split(":", 1)[0] if ":" in cadastral else None
    if region in REGIONS_BY_CODE:
        values["region_slug"] = values.get("region_slug") or region
        values["region_name"] = values.get("region_name") or REGIONS_BY_CODE[region].name
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


def _repair_state(session, record, source, apply):
    query = select(CanonicalLot).where(
        CanonicalLot.id == source.canonical_lot_id if source is not None
        else CanonicalLot.legacy_processed_lot_id == record.id
    )
    canonical = session.scalar(query.with_for_update() if apply else query)
    links = session.scalar(select(func.count()).select_from(SourceLot).where(
        SourceLot.canonical_lot_id == canonical.id,
    )) if canonical is not None else 0
    values = [[column.name, _plain(getattr(canonical, column.name))]
              for column in CanonicalLot.__table__.columns] if canonical is not None else []
    owner_query = select(CanonicalLot).where(CanonicalLot.legacy_processed_lot_id == record.id)
    owner = session.scalar(owner_query.with_for_update() if apply else owner_query)
    owner_values = [[column.name, _plain(getattr(owner, column.name))]
                    for column in CanonicalLot.__table__.columns] if owner is not None else []
    state = hashlib.sha256(json.dumps([_state(record, source), values, links, owner_values], sort_keys=True, default=str).encode()).hexdigest()
    return state, canonical, links


def repair_reported_lots(
    session, *, evidence: list[dict], apply: bool = False,
    allow_reviewed_source_fields: bool = False, expected_states: dict[int, str] | None = None,
    recheck_geo_ids: set[int] | None = None, last_report: int = 44,
) -> dict[str, Any]:
    reported_ids = set(session.scalars(select(LotIssueReport.lot_id).where(LotIssueReport.id <= last_report)))
    return _repair_exact_lots(
        session, evidence=evidence, allowed_lot_ids=reported_ids, apply=apply,
        allow_reviewed_source_fields=allow_reviewed_source_fields,
        expected_states=expected_states, recheck_geo_ids=recheck_geo_ids,
    )


def _repair_exact_lots(
    session, *, evidence: list[dict], allowed_lot_ids: set[int], apply: bool = False,
    allow_reviewed_source_fields: bool = False, expected_states: dict[int, str] | None = None,
    recheck_geo_ids: set[int] | None = None, audit_scope: str = "report",
) -> dict[str, Any]:
    """Never insert lots, merge identities, clear reviews, or reconcile a source."""
    if not evidence or len(evidence) > 100:
        raise ValueError("Repair requires 1..100 exact lots")
    ids = [int(item["lot_id"]) for item in evidence]
    if len(ids) != len(set(ids)) or any(value <= 0 for value in ids):
        raise ValueError("Repair identities must be unique positive lot IDs")
    if not set(ids) <= allowed_lot_ids:
        raise ValueError("Repair includes a lot outside the exact repair allowlist")
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
        state, canonical, links = _repair_state(session, record, source, apply)
        if apply and (expected_states is None or expected_states.get(lot_id) != state):
            raise ValueError(f"Repair preview is stale: {lot_id}")
        source_before = {column.name: getattr(source, column.name) for column in SourceLot.__table__.columns} if source else {}
        old_region_code = record.region_code
        observed = set(lot.raw_data.get("repair_observed_fields", DEFAULT_REPORT_FIELDS))
        metadata = ("region_slug", "region_name", "detail_level") if audit_scope == "report" or "cadastral_number" in observed else ("detail_level",)
        observed.update(name for name in metadata if getattr(lot, name) is not None)
        observed &= set(SOURCE_FIELDS)
        changes = {}
        for name in SOURCE_FIELDS:
            if name not in observed:
                continue
            current = getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None)
            value = getattr(lot, name)
            if _plain(current) != _plain(value):
                changes[name] = {"before": _plain(current), "after": _plain(value)}
        if lot.auction_status == "closed" and not record.is_archived:
            changes["is_archived"] = {"before": False, "after": True}
        protected = record.review_status is not None and not allow_reviewed_source_fields
        different_asset = (
            bool("cadastral_number" in observed and lot.cadastral_number and canonical is not None and canonical.cadastral_number != lot.cadastral_number)
            or ("category" in observed and lot.category in {"receivable", "receivables", "debt", "claim", "car", "vehicle", "equipment", "transport", "movable"})
        )
        entry = {"lot_id": lot_id, "source_system": lot.source_system, "external_id": lot.external_id,
                 "review_status": record.review_status, "protected": protected, "expected_state": state,
                 "changes": changes, "exclude_from_property_map": not is_sale_real_estate_lot(lot)}
        entry["restore_source_provenance"] = source is None or source.processed_lot_id is None
        entry["isolate_canonical"] = bool(canonical is not None and links and links > 1 and different_asset)
        entry["recheck_geo"] = lot_id in (recheck_geo_ids or set())
        items.append(entry)
        if not apply or protected:
            continue
        # Unobserved fields retain their existing source values; public/auth-limited pages
        # do not erase historical dates or prices merely because they hide them.
        for name in SOURCE_FIELDS:
            if name not in observed:
                setattr(lot, name, getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None))
        if source is not None:
            # Partial public evidence must not clear procedure, party or contact
            # fields that are outside this repair's explicit field scope.
            for field in fields(NormalizedLot):
                if field.name not in SOURCE_FIELDS and field.name != "raw_data" and hasattr(source, field.name):
                    setattr(lot, field.name, getattr(source, field.name))
            lot.raw_data = {**(source.raw_data or {}), **lot.raw_data}
        canonical_before = ({name: getattr(canonical, name) for name in
                            ("id", "title", "category", "address", "cadastral_number", "region_code", "area")}
                            if canonical is not None else {})
        if canonical is not None and entry["isolate_canonical"]:
            # A previously poisoned source row must not overwrite another asset's canonical.
            previous_owner = session.scalar(select(CanonicalLot).where(
                CanonicalLot.legacy_processed_lot_id == lot_id,
            ).with_for_update())
            if previous_owner is not None:
                previous_owner.legacy_processed_lot_id = None
                session.flush()
            canonical = CanonicalLot(canonical_key=f"source-repair:{lot.source_system}:{lot.external_id}",
                                     legacy_processed_lot_id=lot_id, title=lot.title,
                                     category=lot.category, address=lot.address, cadastral_number=lot.cadastral_number)
            session.add(canonical)
            session.flush()
            if source is not None:
                source.canonical_lot_id = canonical.id
        for name in observed & PROCESSED_FIELDS:
            if name == "auction_status":
                apply_lot_status(session, record, lot.auction_status, f"exact_{audit_scope}_repair")
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
        # _sync_source_lot is a full projection writer. A partial source repair
        # must preserve each projection's own unobserved values independently,
        # rather than copying an unverified ProcessedLot value into SourceLot.
        changed_source = (observed & {column.name for column in SourceLot.__table__.columns}) | {
            "canonical_lot_id", "processed_lot_id", "raw_data", "last_seen_at", "missing_successful_runs",
        }
        if "auction_status" in observed:
            changed_source.update({"source_status", "is_active", "is_archived", "archived_at", "archive_reason"})
        region_observed = bool(observed & {"region_slug", "region_name", "cadastral_number"})
        if region_observed:
            changed_source.add("region_code")
        else:
            record.region_code = old_region_code
        for name, value in source_before.items():
            if name not in changed_source:
                setattr(source, name, value)
        if canonical.id == canonical_before.get("id"):
            for name, value in canonical_before.items():
                if name != "id" and name not in observed and not (name == "region_code" and region_observed):
                    setattr(canonical, name, value)
        record.last_update = utc_now()
        if lot.auction_status == "closed":
            record.is_archived = True
            record.closed_at = record.closed_at or utc_now()
            record.archived_at = record.archived_at or utc_now()
        if entry["exclude_from_property_map"]:
            record.is_archived = source.is_archived = True
            source.is_active = False
            source.archive_reason = f"exact_{audit_scope}_non_property_sale"
            source.archived_at = source.archived_at or utc_now()
        if entry["exclude_from_property_map"] or any(name in changes for name in ("address", "cadastral_number", "title", "description")) or lot_id in (recheck_geo_ids or set()):
            record.current_geo_lat = record.current_geo_lon = None
            record.current_geo_source = record.current_geo_confidence = None
            record.current_geo_observed_at = record.geo_input_hash = None
            record.needs_geo_check = lot.auction_status in {"active", "scheduled"} and not entry["exclude_from_property_map"]
            failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot_id))
            if failure is not None:
                session.delete(failure)
        log_action(session, "operator", f"exact_{audit_scope}_source_repair", "lot", str(lot_id),
                   {"changes": changes, "review_status_preserved": record.review_status,
                    "external_id": lot.external_id, "source_system": lot.source_system})
    return {"apply": apply, "selected": len(ids), "protected": sum(bool(item.get("protected")) for item in items),
            "blocked": sum(bool(item.get("blocked")) for item in items), "items": items,
            "reconcile_missing": False, "inserted_lots": 0}
