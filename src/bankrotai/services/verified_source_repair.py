"""Bounded whole-database repair from freshly verified exact-source evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from bankrotai.services.asset_category import classify_category
from bankrotai.services.reported_lot_repair import SOURCE_FIELDS, _repair_exact_lots


def repair_verified_source_lots(
    session, *, evidence: list[dict], allowed_lot_ids: set[int],
    apply: bool = False, expected_states: dict[int, str] | None = None,
    expected_evidence_sha256: str | None = None,
) -> dict:
    if not evidence or len(evidence) > 100:
        raise ValueError("Verified repair requires 1..100 exact source records")
    now = datetime.now(timezone.utc)
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
        if (url.scheme != "https" or not source or url.username or url.password
                or not (url.hostname == source or str(url.hostname).endswith("." + source))):
            raise ValueError("Source evidence URL is not on the exact source domain")
        if proof.get("external_id") != lot.get("external_id"):
            raise ValueError("Source proof and normalized identity differ")
        raw = lot.get("raw_data", {})
        if raw.get("repair_evidence_sha256") != proof["sha256"]:
            raise ValueError("Normalized evidence references a different source snapshot")
        observed = set(raw.get("repair_observed_fields", []))
        if not observed or not observed <= set(SOURCE_FIELDS):
            raise ValueError("Explicit bounded observed field scope is required")
        if not observed <= set(proof.get("observed_fields", [])) | {"category", "detail_level"}:
            raise ValueError("Repair includes a field absent from source evidence")
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
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
    if apply and expected_evidence_sha256 != digest:
        raise ValueError("Evidence does not match the reviewed preview")
    result = _repair_exact_lots(
        session, evidence=evidence, allowed_lot_ids=allowed_lot_ids, apply=apply,
        expected_states=expected_states, allow_reviewed_source_fields=True,
        audit_scope="verified_source",
    )
    result["evidence_sha256"] = digest
    return result
