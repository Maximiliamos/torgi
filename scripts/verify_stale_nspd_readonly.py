"""Journal primary NSPD responses for the complete eligible stale no-match cohort.

No database, queue, map or provider-health writes. One request per second,
no retries, stop on any transport/access error. A response is evidence, not
permission to assign coordinates: the normal GEO validator is still required.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import time

import requests
from sqlalchemy import text

from bankrotai.db import SessionLocal
from bankrotai.geo import nspd_tls_verify
from bankrotai.services.cadastral_identity import pick_nspd_feature
from bankrotai.services.geo_resilience import require_provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-cadastre", default="")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20000:
        raise ValueError("Expected 1..20000 distinct cadastral queries")
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        targets = [dict(r) for r in session.execute(text("""
            SELECT p.cadastral_number, array_agg(p.id ORDER BY p.id) lot_ids
            FROM processed_lots p JOIN geo_failures g ON g.lot_id=p.id
            WHERE p.duplicate_of_id IS NULL AND NOT p.is_archived
              AND p.auction_status IN ('active','scheduled')
              AND p.current_geo_lat IS NULL AND p.current_geo_lon IS NULL
              AND p.review_status IS NULL AND g.status='deferred_no_match'
              AND p.cadastral_number ~ '^[0-9]{2}:[0-9]{1,2}:[0-9]{5,9}:[0-9]{1,12}$'
              AND p.cadastral_number > :after
            GROUP BY p.cadastral_number ORDER BY p.cadastral_number LIMIT :limit
        """), {"after": args.after_cadastre, "limit": args.limit}).mappings()]
        session.rollback()
    print(json.dumps({"event": "cohort", "distinct_cadastres": len(targets),
                      "lot_count": sum(len(t["lot_ids"]) for t in targets),
                      "database_writes": 0, "complete_source_inventory": False}), flush=True)
    client = requests.Session()
    previous = 0.0
    for target in targets:
        time.sleep(max(0, 1.0 - (time.monotonic() - previous)))
        receipt = {**target, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                   "database_writes": 0, "provider": "nspd"}
        try:
            require_provider("nspd", external=True)
            previous = time.monotonic()
            response = client.get(
                "https://nspd.gov.ru/api/geoportal/v2/search/geoportal",
                params={"thematicSearchId": 1, "query": target["cadastral_number"]},
                headers={"Referer": "https://nspd.gov.ru/map?thematic=PKK",
                         "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
                         "Accept": "application/json,text/plain,*/*"},
                timeout=(5, 20), verify=nspd_tls_verify(),
            )
            receipt.update(http_status=response.status_code,
                           response_sha256=hashlib.sha256(response.content).hexdigest(),
                           response_base64=base64.b64encode(response.content).decode())
            if response.status_code == 404:
                receipt["result"] = "query_scoped_no_match"
            else:
                response.raise_for_status()
                payload = response.json()
                features = (payload.get("data") or {}).get("features") or payload.get("features") or []
                feature = pick_nspd_feature(features, target["cadastral_number"])
                receipt["result"] = "exact_feature" if feature else "no_exact_feature"
        except Exception as exc:
            receipt.update(result="operational_stop", error_type=type(exc).__name__)
            print(json.dumps(receipt, ensure_ascii=True), flush=True)
            raise SystemExit("NSPD refused/unavailable; remaining cohort stays unverified") from None
        print(json.dumps(receipt, ensure_ascii=True), flush=True)
    print(json.dumps({"event": "cohort_finished", "checked": len(targets), "database_writes": 0}), flush=True)


if __name__ == "__main__":
    main()
