"""Only the two exact technical spellings of an M-ETS trade/lot identity."""
from dataclasses import replace
import re

from sqlalchemy import select

from bankrotai.db import ProcessedLot


def mets_native_identity(source_system, external_id):
    if source_system != "m-ets.ru" or not isinstance(external_id, str):
        return None
    native = external_id.removeprefix("m-ets.ru:")
    return native if re.fullmatch(r"[1-9]\d{3,8}-[1-9]\d{0,4}", native) else None


def same_mets_alias_identity(left, right):
    native = mets_native_identity(getattr(left, "source_system", None), getattr(left, "external_id", None))
    return bool(native and getattr(left, "external_id", None) != getattr(right, "external_id", None)
                and native == mets_native_identity(getattr(right, "source_system", None), getattr(right, "external_id", None)))


def publication_presence_ids(source_system, external_id):
    native = mets_native_identity(source_system, external_id)
    return {native, "m-ets.ru:"+native} if native else {external_id}


def source_presence_identities(lot):
    return publication_presence_ids(lot.source_system, lot.external_id)


def resolve_mets_alias_lots(session, lots):
    """Refresh the existing projection, preserving its ID and user decision.

    Existing unlinked pairs require the separate, audited repair. Explicit
    alias links select their primary; grades are never transferred. A new lot
    uses the provider's bare ID, and a lone legacy spelling retains its ID.
    """
    native_ids = {n for lot in lots if (n := mets_native_identity(lot.source_system, lot.external_id))}
    if not native_ids:
        return lots
    aliases = sorted({a for n in native_ids for a in (n, "m-ets.ru:"+n)})
    existing = {}
    for offset in range(0, len(aliases), 500):
        existing.update({p.external_id: p for p in session.scalars(select(ProcessedLot).where(
            ProcessedLot.source_system == "m-ets.ru",
            ProcessedLot.external_id.in_(aliases[offset:offset+500]),
        ))})
    resolved = []
    seen = set()
    for lot in lots:
        native = mets_native_identity(lot.source_system, lot.external_id)
        if not native:
            resolved.append(lot)
            continue
        pair = [existing[key] for key in (native, "m-ets.ru:"+native) if key in existing]
        target = None
        if len(pair) == 1 and pair[0].duplicate_of_id is None:
            target = pair[0].external_id
        elif len(pair) == 2:
            for primary, secondary in (pair, pair[::-1]):
                if (primary.duplicate_of_id is None and secondary.duplicate_of_id == primary.id
                        and secondary.review_status is None and same_mets_alias_identity(primary, secondary)):
                    target = primary.external_id
                    break
        elif not pair:
            target = native
        if target and target != lot.external_id:
            lot = replace(lot, external_id=target, raw_data={**lot.raw_data,
                "primary_provider_external_id": native, "stored_id_spelling_verified": True,
                "ingestion_input_external_id": lot.external_id})
        key = (lot.source_system, lot.external_id)
        if key in seen:
            raise ValueError("Multiple M-ETS inputs resolve to one publication; refuse ambiguous writes")
        seen.add(key)
        resolved.append(lot)
    return resolved
