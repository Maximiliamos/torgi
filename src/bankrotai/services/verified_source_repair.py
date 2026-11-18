"""Source-backed corrections for arbitrary existing lots with immutable evidence hashes."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from bankrotai.db import CanonicalLot, GeoFailure, ProcessedLot, SourceLot, utc_now
from bankrotai.logic import _sync_source_lot, apply_lot_status, log_action
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot
from bankrotai.services.reported_lot_repair import (
    MONEY_FIELDS,
    PROCESSED_FIELDS,
    SOURCE_FIELDS,
    _plain,
    _state,
    decode_lot,
)


def evidence_sha256(normalized: dict[str, Any]) -> str:
    """Match the hash emitted by reports-source-evidence.py."""
    payload = json.dumps(normalized, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _observed_fields(lot) -> set[str]:
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    observed = raw.get("repair_observed_fields")
    if not isinstance(observed, list) or not observed:
        raise ValueError("Verified repair requires explicit repair_observed_fields")
    unknown = {str(name) for name in observed} - set(SOURCE_FIELDS)
    if unknown:
        raise ValueError(f"Verified repair contains unsupported observed fields: {sorted(unknown)}")
    return {str(name) for name in observed}


def repair_verified_source_lots(
    session,
    *,
    evidence: list[dict[str, Any]],
    apply: bool = False,
    expected_states: dict[int, str] | None = None,
    allow_reviewed_source_fields: bool = False,
) -> dict[str, Any]:
    """Repair existing exact identities only; never insert, delete, or infer absent fields."""
    if not evidence or len(evidence) > 500:
        raise ValueError("Verified repair requires 1..500 evidence records")

    items: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str]] = set()
    for item in evidence:
        normalized = item.get("normalized")
        digest = str(item.get("sha256") or "").strip().lower()
        if item.get("error") or not isinstance(normalized, dict):
            items.append({
                "lot_id": item.get("lot_id"),
                "blocked": "source_evidence_unavailable",
            })
            continue
        if len(digest) != 64 or digest != evidence_sha256(normalized):
            raise ValueError("Verified repair evidence SHA-256 mismatch")

        lot = decode_lot(normalized)
        identity = (str(lot.source_system or "").strip(), str(lot.external_id or "").strip())
        if not all(identity) or identity in seen_identities:
            raise ValueError("Verified repair identities must be unique and non-empty")
        seen_identities.add(identity)
        observed = _observed_fields(lot)

        record_query = select(ProcessedLot).where(
            ProcessedLot.source_system == identity[0],
            ProcessedLot.external_id == identity[1],
        )
        record = session.scalar(record_query.with_for_update() if apply else record_query)
        if record is None:
            raise ValueError(f"Verified source identity is not present: {identity[0]}:{identity[1]}")
        if item.get("lot_id") is not None and int(item["lot_id"]) != record.id:
            raise ValueError(f"Verified evidence lot_id does not match exact identity: {record.id}")

        source_query = select(SourceLot).where(
            SourceLot.source_system == identity[0],
            SourceLot.external_id == identity[1],
        )
        source = session.scalar(source_query.with_for_update() if apply else source_query)
        if source is None or source.processed_lot_id != record.id:
            raise ValueError(f"Verified source projection link is missing or ambiguous: {record.id}")

        state = _state(record, source)
        if apply and (expected_states is None or expected_states.get(record.id) != state):
            raise ValueError(f"Verified repair preview is stale: {record.id}")

        changes: dict[str, dict[str, Any]] = {}
        for name in observed:
            current = getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None)
            value = getattr(lot, name)
            if _plain(current) != _plain(value):
                changes[name] = {"before": _plain(current), "after": _plain(value)}
        if lot.auction_status == "closed" and not record.is_archived:
            changes["is_archived"] = {"before": False, "after": True}

        protected = record.review_status is not None and not allow_reviewed_source_fields
        entry = {
            "lot_id": record.id,
            "source_system": identity[0],
            "external_id": identity[1],
            "review_status": record.review_status,
            "protected": protected,
            "expected_state": state,
            "evidence_sha256": digest,
            "observed_fields": sorted(observed),
            "changes": changes,
            "exclude_from_property_map": not is_sale_real_estate_lot(lot),
        }
        items.append(entry)
        if not apply or protected or not changes:
            continue

        # Keep every unobserved field from the current projections. A source page
        # that hides a price/date cannot erase a previously observed value.
        for name in SOURCE_FIELDS:
            if name in observed:
                continue
            value = getattr(record, name) if name in PROCESSED_FIELDS else getattr(source, name, None)
            setattr(lot, name, value)

        canonical = session.get(CanonicalLot, source.canonical_lot_id)
        if canonical is None:
            raise ValueError(f"Verified source canonical link is missing: {record.id}")
        links = session.scalar(
            select(func.count()).select_from(SourceLot).where(
                SourceLot.canonical_lot_id == canonical.id
            )
        ) or 0
        if (
            links > 1
            and "cadastral_number" in observed
            and lot.cadastral_number
            and canonical.cadastral_number != lot.cadastral_number
        ):
            if canonical.legacy_processed_lot_id == record.id:
                canonical.legacy_processed_lot_id = None
                session.flush()
            canonical = CanonicalLot(
                canonical_key=f"verified-source-repair:{identity[0]}:{identity[1]}",
                legacy_processed_lot_id=record.id,
                title=lot.title,
                category=lot.category,
                address=lot.address,
                cadastral_number=lot.cadastral_number,
            )
            session.add(canonical)
            session.flush()
            source.canonical_lot_id = canonical.id

        for name in observed & PROCESSED_FIELDS:
            if name == "auction_status":
                apply_lot_status(session, record, lot.auction_status, "verified_source_repair")
            else:
                value = getattr(lot, name)
                setattr(
                    record,
                    name,
                    Decimal(str(value)) if name in MONEY_FIELDS and value is not None else value,
                )
        raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
        if "cadastral_number" in observed and raw.get("cadastral_numbers"):
            record.cadastral_numbers = list(raw["cadastral_numbers"])

        source = _sync_source_lot(session, record, lot, canonical_hint=canonical, isolate_source=True)
        canonical = session.get(CanonicalLot, source.canonical_lot_id)
        if canonical is None:
            raise ValueError(f"Verified source canonical disappeared during repair: {record.id}")
        if "category" in observed:
            canonical.category = lot.category
        if "address" in observed:
            canonical.address = lot.address
        if "cadastral_number" in observed:
            canonical.cadastral_number = lot.cadastral_number

        record.last_update = utc_now()
        if lot.auction_status == "closed":
            record.is_archived = True
            record.closed_at = record.closed_at or utc_now()
            record.archived_at = record.archived_at or utc_now()

        if entry["exclude_from_property_map"]:
            record.is_archived = source.is_archived = True
            source.is_active = False
            source.archive_reason = "verified_source_non_property_sale"
            source.archived_at = source.archived_at or utc_now()

        geo_fields = {"address", "cadastral_number", "title", "description"}
        if observed & geo_fields and any(name in changes for name in geo_fields):
            record.current_geo_lat = record.current_geo_lon = None
            record.current_geo_source = record.current_geo_confidence = None
            record.current_geo_observed_at = record.geo_input_hash = None
            record.needs_geo_check = (
                lot.auction_status in {"active", "scheduled"}
                and not entry["exclude_from_property_map"]
            )
            failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == record.id))
            if failure is not None:
                session.delete(failure)

        log_action(
            session,
            "operator",
            "verified_source_repair",
            "lot",
            str(record.id),
            {
                "source_system": identity[0],
                "external_id": identity[1],
                "evidence_sha256": digest,
                "observed_fields": sorted(observed),
                "changes": changes,
                "review_status_preserved": record.review_status,
            },
        )

    return {
        "apply": apply,
        "selected": len(evidence),
        "protected": sum(bool(item.get("protected")) for item in items),
        "blocked": sum(bool(item.get("blocked")) for item in items),
        "changed": sum(bool(item.get("changes")) for item in items),
        "items": items,
        "inserted_lots": 0,
        "deleted_lots": 0,
        "reconcile_missing": False,
    }
