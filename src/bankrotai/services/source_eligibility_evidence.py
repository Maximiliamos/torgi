"""Keep positive exclusion evidence when a fresh row leaves the property scope.

Ignoring a rejected catalogue row left its previously imported property copy
on the map forever. Record only explicit primary-object or rental evidence;
missing fields and transport/detail failures are never negative evidence.
"""
import hashlib
import json
import re

from sqlalchemy import select

from bankrotai.db import SourceLot, utc_now
from bankrotai.services.asset_category import explicit_nonproperty_category
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot
from bankrotai.services.mets_identity_aliases import publication_presence_ids

FLAG = "source_sale_eligibility"
PROOF = "source_sale_eligibility_evidence"


def exclusion_reason(lot):
    category = explicit_nonproperty_category(lot.title or "")
    if category:
        return "explicit_primary_" + category
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    original_value = raw.get("raw")
    original = original_value if isinstance(original_value, dict) else {}
    for value in (raw.get("typeTransaction"), original.get("typeTransaction")):
        code = value.get("code") if isinstance(value, dict) else value
        if str(code or "").strip().upper() in {"RENT", "RENTAL", "LEASE"}:
            return "explicit_rental_transaction"
    if re.match(r"\s*(?:аренда|субаренда|прав[оа]\s+аренды)\b", lot.title or "", re.I):
        return "explicit_rental_title"
    return None


def record_exclusions(session, source_system, lots, run_id=None):
    rejected = {lot.external_id: (lot, reason) for lot in lots
                if lot.source_system == source_system and (reason := exclusion_reason(lot))}
    if not rejected:
        return 0
    changed = 0
    for source in session.scalars(select(SourceLot).where(
        SourceLot.source_system == source_system, SourceLot.external_id.in_(rejected),
    )):
        lot, reason = rejected[source.external_id]
        digest = hashlib.sha256(json.dumps({
            "external_id": lot.external_id, "title": lot.title, "reason": reason,
        }, sort_keys=True, default=str).encode()).hexdigest()
        previous = source.raw_data if isinstance(source.raw_data, dict) else {}
        same_verdict = previous.get(FLAG) is False and (previous.get(PROOF) or {}).get("sha256") == digest
        if same_verdict:
            if run_id is not None and source.last_sync_run_id != run_id:
                source.last_sync_run_id = run_id
                source.last_seen_at = utc_now()
                source.missing_successful_runs = 0
                changed += 1
            continue
        source.raw_data = {**previous, FLAG: False, PROOF: {
            "external_id": lot.external_id, "title": lot.title, "reason": reason,
            "source_url": lot.source_url or lot.lot_url, "sha256": digest,
            "checked_at_utc": utc_now().isoformat(),
        }}
        if run_id is not None:
            source.last_sync_run_id = run_id
            source.last_seen_at = utc_now()
            source.missing_successful_runs = 0
        changed += 1
    return changed


def clear_exclusion(lot):
    raw = dict(lot.raw_data or {})
    raw.pop(FLAG, None)
    raw.pop(PROOF, None)
    lot.raw_data = raw


def persist_exclusions(session_factory, source_system, lots, run_id=None):
    with session_factory() as session:
        if record_exclusions(session, source_system, lots, run_id):
            session.commit()


def observe_rejection(result, external_id):
    if external_id not in result.seen_external_ids:
        result.items_rejected += 1
        result.items_seen += 1
        result.seen_external_ids.update(publication_presence_ids(getattr(result, "source_system", None), external_id))
    else:
        result.items_duplicates += 1


def partition_sale_lots(result, lots):
    accepted = []
    for lot in lots:
        if is_sale_real_estate_lot(lot):
            accepted.append(lot)
        else:
            observe_rejection(result, lot.external_id)
    return accepted
