"""Prepare hash-verified TBankrot repair evidence from a read-only verifier output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bankrotai.services.verified_source_repair import evidence_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.verification).read_text(encoding="utf-8-sig"))
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise ValueError("Verification payload must contain items")

    verified: list[dict] = []
    blocked: list[dict] = []
    identities: set[tuple[str, str]] = set()

    for item in items:
        if not isinstance(item, dict):
            blocked.append({"error": "invalid_item"})
            continue
        if item.get("source_system") != "tbankrot.ru":
            blocked.append({
                "lot_id": item.get("lot_id"),
                "error": "not_tbankrot",
            })
            continue
        normalized = item.get("normalized")
        digest = str(item.get("sha256") or "").strip().lower()
        html_digest = str(item.get("detail_html_sha256") or "").strip().lower()
        observed = item.get("observed_fields")
        if item.get("error") or not item.get("success"):
            blocked.append({
                "lot_id": item.get("lot_id"),
                "source_system": item.get("source_system"),
                "external_id": item.get("external_id"),
                "error": item.get("error") or "verification_unsuccessful",
            })
            continue
        if not isinstance(normalized, dict) or evidence_sha256(normalized) != digest:
            raise ValueError(f"Normalized evidence hash mismatch for lot {item.get('lot_id')}")
        if len(html_digest) != 64:
            raise ValueError(f"Detail HTML SHA-256 is missing for lot {item.get('lot_id')}")
        raw = normalized.get("raw_data") if isinstance(normalized.get("raw_data"), dict) else {}
        raw_observed = raw.get("repair_observed_fields")
        if not isinstance(observed, list) or not observed or raw_observed != observed:
            raise ValueError(f"Observed-field manifest mismatch for lot {item.get('lot_id')}")
        identity = (
            str(item.get("source_system") or "").strip(),
            str(item.get("external_id") or "").strip(),
        )
        if not all(identity) or identity in identities:
            raise ValueError("Duplicate or empty exact source identity in verification output")
        identities.add(identity)
        verified.append({
            "lot_id": int(item["lot_id"]),
            "source_system": identity[0],
            "external_id": identity[1],
            "observed_at": item.get("observed_at"),
            "detail_url": item.get("detail_url"),
            "detail_html_sha256": html_digest,
            "normalized": normalized,
            "sha256": digest,
        })

    result = {
        "database_writes": 0,
        "source_system": "tbankrot.ru",
        "verified": len(verified),
        "blocked": len(blocked),
        "items": verified,
        "blocked_items": blocked,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "verified": len(verified),
        "blocked": len(blocked),
        "output": str(output),
    }))


if __name__ == "__main__":
    main()
