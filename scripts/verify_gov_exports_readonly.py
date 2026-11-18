"""Verify database identities through bounded official GIS exports.

Exports stop at 500 rows. Split date windows (then known subject codes), never
infer missing/closed lots from absence, and never write the database. Journals
and checksummed XLSX/row snapshots support resumption and separate repair.
"""
import argparse
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

from bankrotai.scrapers import TorgiGovClient
from bankrotai.scraper_contracts import TorgiGovSearchFilters
from bankrotai.services.asset_category import classify_category
from bankrotai.services.torgi_gov_normalization import apply_export_transaction_scope

STATUSES = "PUBLISHED,APPLICATIONS_SUBMISSION,APPLICATIONS_SUBMISSION_SUSPENDED,DETERMINING_WINNER,SUCCEED,FAILED,CANCELED"


def checked_rows(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def make_evidence(lot, target, checked, export_sha):
    raw = lot.raw_data
    original = raw["raw"]
    row_bytes = json.dumps(original, sort_keys=True, default=str, ensure_ascii=False).encode()
    digest = hashlib.sha256(row_bytes).hexdigest()
    observed = {"detail_level", "source_url", "lot_url"}
    for field in ("title", "description", "cadastral_number", "notice_number", "deposit_amount",
                  "auction_step_amount", "application_start_at", "application_deadline", "auction_at", "auction_timezone"):
        if getattr(lot, field) not in (None, ""):
            observed.add(field)
    if original.get("Местонахождение имущества"):
        observed.add("address")
    if lot.start_price is not None and lot.start_price > 0:
        observed.add("start_price")
    final = TorgiGovClient()._coerce_price(original.get("Итоговая цена"))
    if final is not None and final > 0:
        lot.current_price = final
        observed.add("current_price")
    if lot.auction_status not in ("", "unknown"):
        observed.add("auction_status")
    derived = {"detail_level"}
    if {"title", "description"} <= observed and classify_category(lot.title, "") != "other":
        lot.category = classify_category(lot.title, lot.description)
        observed.add("category")
        derived.add("category")
    lot.detail_level = "official_export"
    raw.update(repair_observed_fields=sorted(observed), repair_evidence_sha256=digest,
               repair_evidence_checked_at=checked, official_export_sha256=export_sha)
    result = dict(target, lot_id=target["id"], identity_verified=True, sha256=digest,
                  checked_at_utc=checked, database_writes=0, scope="official_export_fields",
                  normalized=asdict(lot), verification={
        "identity_verified": True, "external_id": lot.external_id, "source_url": lot.source_url,
        "sha256": digest, "checked_at_utc": checked,
        "observed_fields": sorted(observed - derived), "derived_fields": sorted(derived),
    })
    return result, row_bytes


def main():
    if sys.platform == "win32":
        import truststore
        truststore.inject_into_ssl()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--direct-journal")
    parser.add_argument("--max-windows", type=int)
    args = parser.parse_args()
    output = Path(args.output)
    captures = output.parent / "gov-export-captures"
    snapshots = output.parent / "gov-export-row-json"
    captures.mkdir(exist_ok=True)
    snapshots.mkdir(exist_ok=True)
    targets = {row["external_id"]: row for row in json.loads(Path(args.targets).read_text(encoding="utf-8-sig"))["items"]}
    completed = {r["external_id"] for r in checked_rows(output) if r.get("identity_verified")}
    if args.direct_journal:
        completed.update(r["external_id"] for r in checked_rows(Path(args.direct_journal)) if r.get("identity_verified"))
    grouped = defaultdict(list)
    for identity, row in targets.items():
        if identity not in completed and row.get("category") and row.get("publication_date"):
            grouped[(str(row["category"]), row["publication_date"][:7])].append(row)
    windows = deque()
    for (category, _month), rows in sorted(grouped.items(), reverse=True):
        days = [date.fromisoformat(r["publication_date"]) for r in rows]
        windows.append((category, min(days), max(days), None, rows))
    client = TorgiGovClient(timeout=(10, 60), rate_limit=(1, 1), allow_html_fallback=False)
    count = 0
    while windows:
        category, start, end, region, candidates = windows.popleft()
        candidates = [r for r in candidates if r["external_id"] not in completed]
        if not candidates:
            continue
        filters = TorgiGovSearchFilters(category_code=category, publish_date_from=start.isoformat(),
                                       publish_date_to=end.isoformat(), subject_rf=region,
                                       lot_status=STATUSES, type_transaction="SALE")
        params, _ = client._build_excel_export_params(filters)
        name = f"{category}-{start}-{end}-{region or 'all'}"
        capture = captures / (name + ".xlsx")
        metadata = captures / (name + ".json")
        try:
            if capture.exists() and metadata.exists():
                proof = json.loads(metadata.read_text())
                body = capture.read_bytes()
                if hashlib.sha256(body).hexdigest() != proof["sha256"]:
                    raise ValueError("Stored official export content changed")
            else:
                client._respect_rate_limit()
                with client.session.get(client.EXCEL_EXPORT_ENDPOINT, params=params, timeout=(10, 60),
                                        stream=True, allow_redirects=False) as response:
                    response.raise_for_status()
                    if response.is_redirect:
                        raise ValueError("Export redirected; source identity unproven")
                    chunks = bytearray()
                    for block in response.iter_content(65536):
                        chunks.extend(block)
                        if len(chunks) > 50 * 1024 * 1024:
                            raise ValueError("Export exceeds bounded 50 MiB budget")
                body = bytes(chunks)
                if not body.startswith(b"PK"):
                    raise ValueError("Official endpoint returned no XLSX archive")
                proof = {"sha256": hashlib.sha256(body).hexdigest(), "params": params,
                         "checked_at_utc": datetime.now(timezone.utc).isoformat()}
                capture.write_bytes(body)
                metadata.write_text(json.dumps(proof), encoding="utf8")
            lots = client._parse_excel_export(body)
            apply_export_transaction_scope(lots, "SALE")
            matched = 0
            for lot in lots:
                target = targets.get(lot.external_id)
                if target is None or lot.external_id in completed:
                    continue
                receipt, row_bytes = make_evidence(lot, target, proof["checked_at_utc"], proof["sha256"])
                (snapshots / (lot.external_id.split(":", 1)[1] + "-" + receipt["sha256"] + ".json")).write_bytes(row_bytes)
                with output.open("a", encoding="utf8") as handle:
                    handle.write(json.dumps(receipt, default=str, ensure_ascii=True) + "\n")
                completed.add(lot.external_id)
                matched += 1
            remaining = [r for r in candidates if r["external_id"] not in completed]
            if len(lots) >= 500 and remaining:
                if start < end:
                    midpoint = start + (end - start) // 2
                    for first, last in ((start, midpoint), (midpoint + timedelta(days=1), end)):
                        subset = [r for r in remaining if first <= date.fromisoformat(r["publication_date"]) <= last]
                        if subset:
                            windows.appendleft((category, first, last, region, subset))
                elif region is None:
                    regions = defaultdict(list)
                    for r in remaining:
                        if r.get("region"):
                            regions[str(r["region"])].append(r)
                    for code, subset in regions.items():
                        windows.appendleft((category, start, end, code, subset))
            count += 1
            print(json.dumps({"window": name, "rows": len(lots), "matched": matched,
                              "verified_ids_including_direct": len(completed), "windows_remaining": len(windows),
                              "complete_source_inventory": False, "database_writes": 0}), flush=True)
            if args.max_windows and count >= args.max_windows:
                break
            time.sleep(0.05)
        except (requests.RequestException, ValueError) as exc:
            print(json.dumps({"window": name, "error": str(exc)[:400], "database_writes": 0}), flush=True)
            raise SystemExit("Official source refused/unavailable/schema changed; remaining fields stay unverified") from exc


if __name__ == "__main__":
    main()
