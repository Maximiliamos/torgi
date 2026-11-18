"""Resume exact-ID official GIS detail verification; journal evidence, never write DB.

Input is an exported exact database identity list. No missing-lot inference,
cross-sale cadastre matching, credentials, or reconciliation is performed.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import sys

import requests

from bankrotai.scrapers import TorgiGovClient


def main():
    if sys.platform == "win32":
        import truststore
        truststore.inject_into_ssl()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    targets = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))["items"]
    if any(row["source_system"] != "torgi.gov.ru" for row in targets):
        raise ValueError("Only exact official GIS identities are accepted")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    captures = output.parent / "gov-primary-json"
    captures.mkdir(exist_ok=True)
    completed = set()
    if output.exists():
        completed = {row["external_id"] for row in map(json.loads, output.read_text().splitlines())
                     if row.get("identity_verified") or row.get("http_status") in (404, 410)}
    client = TorgiGovClient(timeout=(5, 25), rate_limit=(1, 1), allow_html_fallback=False)
    errors = attempts = 0
    for target in targets:
        identity = target["external_id"]
        if identity in completed:
            continue
        result = dict(target, lot_id=target["id"], database_writes=0,
                      checked_at_utc=datetime.now(timezone.utc).isoformat())
        stop = False
        try:
            lot = client.fetch_lot_detail(identity)
            if lot.external_id != identity:
                raise ValueError("Exact official identity mismatch")
            payload = lot.raw_data["raw"]
            body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            digest = hashlib.sha256(body).hexdigest()
            (captures / (identity.split(":", 1)[1] + "-" + digest + ".json")).write_bytes(body)
            observed = {"detail_level", "source_url", "lot_url"}
            for name in ("title", "description", "address", "start_price", "cadastral_number",
                         "application_deadline", "auction_at", "auction_timezone"):
                if getattr(lot, name) not in (None, ""):
                    observed.add(name)
            # The normalizer's current-price fallback is not independent proof.
            if client._pick_price(payload, "currentPrice", "priceFin", "finalPrice", "actualPrice") is not None:
                observed.add("current_price")
            if lot.auction_status not in ("unknown", ""):
                observed.add("auction_status")
            lot.raw_data.update(repair_observed_fields=sorted(observed), repair_evidence_sha256=digest,
                                repair_evidence_checked_at=result["checked_at_utc"])
            result.update(identity_verified=True, sha256=digest, normalized=asdict(lot), verification={
                "identity_verified": True, "external_id": identity, "source_url": lot.source_url,
                "sha256": digest, "checked_at_utc": result["checked_at_utc"],
                "observed_fields": sorted(observed - {"detail_level"}), "derived_fields": ["detail_level"],
            })
            errors = 0
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            result.update(http_status=status, error_type=type(exc).__name__)
            errors += 1
            stop = status in (401, 403, 429) or errors >= 3
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            result.update(error_type=type(exc).__name__, error=str(exc)[:300])
            errors += 1
            stop = errors >= 3
        with output.open("a", encoding="utf8") as handle:
            handle.write(json.dumps(result, default=str, ensure_ascii=True) + "\n")
        attempts += 1
        print(json.dumps({"attempts": attempts, "external_id": identity,
                          "verified": result.get("identity_verified", False), "stop": stop}), flush=True)
        if stop:
            raise SystemExit("Primary source refused/unavailable; remaining rows stay unverified")
        if args.limit and attempts >= args.limit:
            break
        time.sleep(0.05)


if __name__ == "__main__":
    main()
