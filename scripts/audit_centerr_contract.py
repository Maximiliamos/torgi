#!/usr/bin/env python3
"""Read-only live proof for CenterR lot identity and pagination."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests
from bankrotai.connectors.etp_sources.centerr_contract import (
    BASE_URL,
    PROPERTY_RE,
    CenterRContractError,
    page_fingerprint,
    parse_catalogue_lots,
    parse_detail_facts,
    parse_next_page_postback,
    summarize_catalogue_identities,
    verify_identity,
)


TIMEOUT = 25
MAX_PAGES = 200
USER_AGENT = "TORGI-CenterR-contract-audit/1.0 (+https://github.com/Maximiliamos/torgi)"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _scan(session: requests.Session) -> dict:
    response = session.get(BASE_URL, timeout=TIMEOUT, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"CenterR page 1 HTTP {response.status_code}")

    page = 1
    pages = []
    all_rows = []
    content_hashes: set[str] = set()
    while True:
        digest = page_fingerprint(response.content)
        if digest in content_hashes:
            raise RuntimeError(f"CenterR repeated page content at logical page {page}")
        content_hashes.add(digest)
        rows = parse_catalogue_lots(response.content)
        if not rows:
            raise RuntimeError(f"CenterR page {page} had no structurally valid lot rows")
        pages.append({
            "page": page, "count": len(rows),
            "first_id": rows[0].external_id, "last_id": rows[-1].external_id,
            "sha256": digest,
        })
        all_rows.extend(rows)
        if page >= MAX_PAGES:
            raise RuntimeError(f"CenterR exceeded safety bound of {MAX_PAGES} pages")
        postback = parse_next_page_postback(response.content, next_page=page + 1)
        if postback is None:
            break
        target, argument, data = postback
        data["__EVENTTARGET"] = target
        data["__EVENTARGUMENT"] = argument
        response = session.post(response.url or BASE_URL, data=data, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"CenterR page {page + 1} HTTP {response.status_code}")
        page += 1

    identity_summary = summarize_catalogue_identities(all_rows)
    return {
        "pages": pages,
        "terminal_page": page,
        "row_count": len(all_rows),
        "unique_count": identity_summary["unique_count"],
        "duplicate_same_identity_count": identity_summary["duplicate_same_identity_count"],
        "collisions": identity_summary["collisions"],
        "ids": identity_summary["unique_ids"],
        "rows": all_rows,
        "id_digest": identity_summary["id_digest"],
    }


def _detail_sample(session: requests.Session, rows: list, limit: int) -> dict:
    property_rows = [row for row in rows if PROPERTY_RE.search(row.title)]
    failures = []
    verified_ids = []
    attempted_ids = []
    price_ok = status_ok = title_ok = identity_ok_count = 0
    address_present = cadastre_ok = area_ok = classifier_present = 0
    for row in property_rows:
        if len(verified_ids) >= limit:
            break
        attempted_ids.append(row.external_id)
        response = session.get(row.detail_url, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code != 200:
            failures.append({"id": row.external_id, "reason": f"http_{response.status_code}"})
            continue
        try:
            detail = parse_detail_facts(response.content, detail_url=row.detail_url)
        except CenterRContractError as exc:
            failures.append({"id": row.external_id, "reason": str(exc)})
            continue
        try:
            verify_identity(row, detail)
        except CenterRContractError:
            failures.append({
                "id": row.external_id, "reason": "catalogue_detail_identity_mismatch",
                "catalogue": [row.trade_id, row.lot_number, row.provider_lot_id],
                "detail": [detail.trade_id, detail.lot_number, detail.provider_lot_id],
            })
            continue
        identity_ok_count += 1
        verified_ids.append(row.external_id)
        if row.listing_price is not None and row.listing_price in {detail.start_price, detail.current_price}:
            price_ok += 1
        else:
            failures.append({"id": row.external_id, "reason": "price_mismatch"})
        if _clean(detail.status_text).lower() == _clean(row.status_text).lower():
            status_ok += 1
        else:
            failures.append({"id": row.external_id, "reason": "status_mismatch"})
        if _clean(detail.title).lower() == _clean(row.title).lower():
            title_ok += 1
        else:
            failures.append({"id": row.external_id, "reason": "title_mismatch"})
        if detail.address:
            address_present += 1
        if detail.efrsb_classifier:
            classifier_present += 1
        if not row.cadastral_number or detail.cadastral_number == row.cadastral_number:
            cadastre_ok += 1
        if row.area is None or detail.area == row.area:
            area_ok += 1

    return {
        "available_property_rows": len(property_rows),
        "attempted": len(attempted_ids),
        "verified": len(verified_ids),
        "identity_success": identity_ok_count, "price_success": price_ok,
        "status_success": status_ok, "title_success": title_ok,
        "address_present": address_present, "classifier_present": classifier_present,
        "cadastre_success": cadastre_ok, "area_success": area_ok, "failures": failures,
        "attempted_ids": attempted_ids,
        "verified_ids": verified_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if not 1 <= args.detail_limit <= 100:
        raise SystemExit("--detail-limit must be in 1..100")

    def fresh_session() -> requests.Session:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        return session

    first = _scan(fresh_session())
    second = _scan(fresh_session())
    stable = first["ids"] == second["ids"] and first["id_digest"] == second["id_digest"]
    details = _detail_sample(fresh_session(), first["rows"], args.detail_limit)
    report = {
        "source": "centerr.ru", "catalogue_url": BASE_URL,
        "scan_1": {k: v for k, v in first.items() if k != "rows"},
        "scan_2": {k: v for k, v in second.items() if k != "rows"},
        "two_pass_ids_stable": stable,
        "detail_sample": details,
        "id_collisions": sorted(set(first["collisions"] + second["collisions"])),
        "wrong_detail_substitutions": [
            item for item in details["failures"]
            if item["reason"] == "catalogue_detail_identity_mismatch"
        ],
        "production_enabled": False,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out:
        args.json_out.write_text(rendered + "\n", encoding="utf-8")

    required_details = min(30, details["available_property_rows"])
    ready = (
        stable and first["terminal_page"] >= 2
        and second["terminal_page"] == first["terminal_page"]
        and not report["id_collisions"]
        and details["verified"] >= required_details
        and details["identity_success"] == details["verified"]
        and not report["wrong_detail_substitutions"]
        and details["price_success"] == details["verified"]
        and details["status_success"] == details["verified"]
        and details["title_success"] == details["verified"]
    )
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
