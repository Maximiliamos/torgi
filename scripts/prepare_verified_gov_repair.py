"""Re-normalize SHA-checked official JSON snapshots using the deployed parser.

Stdin: 1..100 receipts with snapshot_base64. Outputs exact-ID repair evidence.
Does not access the network or modify the database. Missing fields are excluded.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import sys

from bankrotai.scrapers import TorgiGovClient


def main():
    receipts = json.load(sys.stdin)
    if not receipts or len(receipts) > 100:
        raise ValueError("Expected 1..100 primary-source snapshots")
    client = TorgiGovClient()
    items = []
    for receipt in receipts:
        body = base64.b64decode(receipt["snapshot_base64"])
        digest = hashlib.sha256(body).hexdigest()
        if digest != receipt["sha256"]:
            raise ValueError("Primary snapshot content changed")
        payload = json.loads(body)
        external = "torgi_gov:" + str(client._external_base_from_payload(payload))
        if external != receipt["external_id"]:
            raise ValueError("Official snapshot identity mismatch")
        lot = client._normalize_api_lot(payload)
        lot.detail_level = "detail"
        observed = {"detail_level", "source_url", "lot_url"}
        for name in ("title", "description", "address", "start_price", "cadastral_number", "published_at",
                     "application_start_at", "application_deadline", "auction_at", "auction_timezone",
                     "deposit_amount", "auction_step_amount", "notice_number"):
            if getattr(lot, name) not in (None, ""):
                observed.add(name)
        if client._pick_price(payload, "currentPrice", "priceFin", "finalPrice", "actualPrice") is not None:
            observed.add("current_price")
        if lot.auction_status not in ("", "unknown"):
            observed.add("auction_status")
        lot.raw_data.update(repair_observed_fields=sorted(observed), repair_evidence_sha256=digest,
                            repair_evidence_checked_at=receipt["checked_at_utc"])
        items.append({"lot_id": receipt["lot_id"], "normalized": asdict(lot), "verification": {
            "identity_verified": True, "external_id": external, "source_url": lot.source_url,
            "sha256": digest, "checked_at_utc": receipt["checked_at_utc"],
            "observed_fields": sorted(observed - {"detail_level"}), "derived_fields": ["detail_level"],
        }})
    print(json.dumps({"items": items, "database_writes": 0}, default=str, ensure_ascii=True))


if __name__ == "__main__":
    main()
