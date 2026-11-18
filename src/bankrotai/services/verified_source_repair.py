"""Bounded whole-database repair from freshly verified exact-source evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit
from sqlalchemy import select
from bankrotai.db import LotSyncRun, SourceLot

from bankrotai.services.asset_category import classify_category
from bankrotai.services.reported_lot_repair import SOURCE_FIELDS, _repair_exact_lots
from bankrotai.services.source_lot_exclusions import SourceLotExcludedError, excluded_ids
from bankrotai.services.source_verification_metadata import verification_value


# Logical connector IDs can differ from their current public host. Keep aliases
# explicit: a captured link to an arbitrary ETP is not evidence for that source.
SOURCE_EVIDENCE_DOMAIN_ALIASES = {
    "torgi-russia.ru": frozenset({"xn----etbpba5admdlad.xn--p1ai"}),
}


def repair_verified_source_lots(
    session, *, evidence: list[dict], allowed_lot_ids: set[int],
    apply: bool = False, expected_states: dict[int, str] | None = None,
    expected_evidence_sha256: str | None = None,
) -> dict:
    if not evidence or len(evidence) > 100:
        raise ValueError("Verified repair requires 1..100 exact source records")
    now = datetime.now(timezone.utc)
    previous_verifications = {}
    run_observations = {}
    recheck_geo_ids = set()
    source_identities: dict[str, set[str]] = {}
    for item in evidence:
        proof = item.get("verification", {})
        lot = item.get("normalized", {})
        if proof.get("identity_verified") is not True:
            raise ValueError("Source identity has not been independently verified")
        if not re.fullmatch(r"[a-f0-9]{64}", str(proof.get("sha256", ""))):
            raise ValueError("Source snapshot SHA256 is required")
        checked = datetime.fromisoformat(proof.get("checked_at_utc", ""))
        if checked.tzinfo is None or not now - timedelta(hours=24) <= checked <= now + timedelta(minutes=5):
            raise ValueError("Source evidence is stale or lacks an explicit timezone")
        url = urlsplit(proof.get("source_url", ""))
        source = lot.get("source_system", "")
        domains = {source, *SOURCE_EVIDENCE_DOMAIN_ALIASES.get(source, ())}
        if (url.scheme != "https" or not source or url.username or url.password
                or not any(url.hostname == domain or str(url.hostname).endswith("." + domain)
                           for domain in domains)):
            raise ValueError("Source evidence URL is not on the exact source domain")
        if (proof.get("external_id") != lot.get("external_id")
                or not isinstance(lot.get("external_id"), str) or not lot["external_id"]):
            raise ValueError("Source proof and normalized identity differ")
        source_identities.setdefault(source, set()).add(lot["external_id"])
        raw = lot.get("raw_data", {})
        if raw.get("repair_evidence_sha256") != proof["sha256"]:
            raise ValueError("Normalized evidence references a different source snapshot")
        observed = set(raw.get("repair_observed_fields", []))
        if not observed or not observed <= set(SOURCE_FIELDS):
            raise ValueError("Explicit bounded observed field scope is required")
        if not observed <= set(proof.get("observed_fields", [])) | {"category", "detail_level"}:
            raise ValueError("Repair includes a field absent from source evidence")
        if "recheck_geo" in item and not isinstance(item["recheck_geo"], bool):
            raise ValueError("GEO recheck must be an explicit boolean")
        if item.get("recheck_geo"):
            if "cadastral_number" not in observed or "cadastral_number" not in proof.get("observed_fields", []):
                raise ValueError("GEO recheck requires freshly observed cadastral identity")
            recheck_geo_ids.add(item["lot_id"])
        if any(lot.get(name) is None or lot.get(name) == "" for name in observed):
            raise ValueError("Missing public fields cannot erase historical facts")
        if "auction_status" in observed and lot.get("auction_status") == "unknown":
            raise ValueError("An unknown source stage cannot replace a verified status")
        if any(lot.get(name) != proof["source_url"] for name in observed & {"source_url", "lot_url"}):
            raise ValueError("Repaired link differs from the verified source URL")
        if "category" in observed:
            if not {"title", "description"} <= set(proof.get("observed_fields", [])):
                raise ValueError("Category correction requires fresh title and description")
            if lot.get("category") != classify_category(lot.get("title", ""), lot.get("description", "")):
                raise ValueError("Category differs from primary-asset evidence")
        existing = session.scalar(select(SourceLot).where(
            SourceLot.source_system == source, SourceLot.external_id == lot["external_id"],
        ))
        existing_raw = existing.raw_data or {} if existing is not None else {}
        if existing is not None:
            stamp = existing_raw.get("ingestion_observed_at")
            observed_at = datetime.fromisoformat(stamp) if stamp else None
            if observed_at is None and existing.last_sync_run_id:
                run_id = existing.last_sync_run_id
                if run_id not in run_observations:
                    run = session.get(LotSyncRun, run_id)
                    run_observations[run_id] = run.started_at if run is not None else None
                observed_at = run_observations[run_id]
            if observed_at is not None:
                observed_at = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at
                if observed_at > checked:
                    raise ValueError("Source evidence is older than the latest primary ingestion observation")
        field_times = dict(existing_raw.get("verified_source_fields") or {})
        previous_verifications[item["lot_id"]] = field_times
        for name in observed:
            stamp = (field_times.get(name) or {}).get("checked_at_utc")
            if not stamp and name in existing_raw.get("repair_observed_fields", []):
                stamp = existing_raw.get("repair_evidence_checked_at")
            if stamp:
                previous = datetime.fromisoformat(stamp)
                if previous.tzinfo is None or previous > checked:
                    raise ValueError(f"Source evidence is older than the verified field: {name}")
    for source, identities in source_identities.items():
        if excluded_ids(session, source, identities):
            raise SourceLotExcludedError("Verified repair cannot restore an explicitly user-excluded source lot")
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
    if apply and expected_evidence_sha256 != digest:
        raise ValueError("Evidence does not match the reviewed preview")
    result = _repair_exact_lots(
        session, evidence=evidence, allowed_lot_ids=allowed_lot_ids, apply=apply,
        expected_states=expected_states, allow_reviewed_source_fields=True,
        recheck_geo_ids=recheck_geo_ids, audit_scope="verified_source",
    )
    result["evidence_sha256"] = digest
    if apply:
        for item in evidence:
            lot, proof = item["normalized"], item["verification"]
            source_row = session.scalar(select(SourceLot).where(
                SourceLot.source_system == lot["source_system"], SourceLot.external_id == lot["external_id"],
            ))
            if source_row is not None:
                verified = dict(previous_verifications[item["lot_id"]])
                verified.update({name: {"sha256": proof["sha256"], "checked_at_utc": proof["checked_at_utc"]}
                                 for name in lot["raw_data"]["repair_observed_fields"]})
                for name in lot["raw_data"]["repair_observed_fields"]:
                    field = "source_status" if name == "auction_status" else name
                    if hasattr(source_row, field):
                        verified[name]["value"] = verification_value(getattr(source_row, field))
                source_row.raw_data = {**source_row.raw_data, "verified_source_fields": verified}
    return result
