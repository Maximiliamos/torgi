"""Read-only Sberbank-AST bankruptcy source-contract audit.

This script imports only the isolated Sberbank-AST module. It performs no
production persistence, reconciliation, archival or scheduler changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import requests

from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
    DISPLAY_LIMIT,
    PAGE_SIZE,
    SberbankAstContractError,
    build_catalogue_request,
    page_digest,
    parse_catalogue_page,
    parse_detail,
)

TIMEOUT = 30.0


def _post_page(session: requests.Session, page: int):
    url, payload = build_catalogue_request(page)
    response = session.post(url, json=payload, timeout=TIMEOUT, allow_redirects=False)
    if response.status_code in {401, 403, 429}:
        raise SberbankAstContractError(f"provider returned HTTP {response.status_code}")
    if response.status_code != 200:
        raise SberbankAstContractError(f"catalogue returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise SberbankAstContractError("catalogue response is not JSON") from exc
    return parse_catalogue_page(data, page=page)


def _detail_evidence(session: requests.Session, lot: Any) -> dict[str, Any]:
    response = session.get(lot.lot_url, timeout=TIMEOUT, allow_redirects=False)
    if response.status_code in {401, 403, 429}:
        return {
            "external_id": lot.external_id,
            "verified": False,
            "reason": f"http_{response.status_code}",
        }
    if response.status_code != 200:
        return {
            "external_id": lot.external_id,
            "verified": False,
            "reason": f"http_{response.status_code}",
        }
    try:
        detail = parse_detail(
            response.content,
            detail_url=lot.lot_url,
            external_id=lot.external_id,
        )
    except SberbankAstContractError as exc:
        return {
            "external_id": lot.external_id,
            "verified": False,
            "reason": str(exc),
        }
    return {
        "external_id": detail.external_id,
        "verified": True,
        "procedure_number": detail.procedure_number,
        "initial_price_present": detail.start_price is not None,
        "current_price_present": detail.current_price is not None,
        "status": detail.auction_status,
        "application_start_present": detail.application_start_at is not None,
        "application_deadline_present": detail.application_deadline is not None,
        "auction_date_present": detail.auction_at is not None,
        "address_present": bool(detail.address),
        "cadastre_present": bool(detail.cadastral_number),
        "area_present": detail.area is not None,
    }


def _page_plan(first, *, full: bool) -> list[int]:
    if full:
        if not first.source_total_proven or first.terminal_page is None:
            raise SberbankAstContractError(
                f"full traversal refused because API total reached display cap {DISPLAY_LIMIT}"
            )
        return list(range(1, first.terminal_page + 1))

    pages = [1]
    if first.api_total_reported > PAGE_SIZE:
        pages.append(2)
    if first.terminal_page and first.terminal_page not in pages:
        pages.append(first.terminal_page)
    return pages


def _walk(
    session: requests.Session,
    *,
    full: bool,
    detail_limit: int,
) -> dict[str, Any]:
    first = _post_page(session, 1)
    pages = _page_plan(first, full=full)
    results = {1: first}
    for page in pages:
        if page == 1:
            continue
        results[page] = _post_page(session, page)

    lots_by_id: dict[str, Any] = {}
    page_digests: dict[str, str] = {}
    raw_rows = 0
    for page in pages:
        result = results[page]
        raw_rows += result.raw_rows
        page_digests[str(page)] = page_digest(result.lots)
        for lot in result.lots:
            previous = lots_by_id.get(lot.external_id)
            if previous is not None:
                raise SberbankAstContractError(
                    f"duplicate accepted lot identity across pages: {lot.external_id}"
                )
            lots_by_id[lot.external_id] = lot

    details = [
        _detail_evidence(session, lot)
        for lot in list(lots_by_id.values())[:detail_limit]
    ]
    verified = [row for row in details if row["verified"]]
    return {
        "api_total_reported": first.api_total_reported,
        "source_total_proven": first.source_total_proven,
        "terminal_page": first.terminal_page,
        "pages_checked": pages,
        "raw_rows_checked": raw_rows,
        "unique_property_ids": len(lots_by_id),
        "page_digests": page_digests,
        "details_requested": len(details),
        "details_verified": len(verified),
        "detail_initial_price_success": sum(row["initial_price_present"] for row in verified),
        "detail_current_price_success": sum(row["current_price_present"] for row in verified),
        "detail_known_status_success": sum(row["status"] != "unknown" for row in verified),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, choices=(1, 2), default=2)
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.details <= 100:
        parser.error("--details must be between 0 and 100")

    session = requests.Session()
    session.headers.setdefault(
        "User-Agent",
        "TORGI-Sberbank-AST-readonly-audit/1.0 (+https://github.com/Maximiliamos/torgi)",
    )

    runs = [
        _walk(session, full=args.full, detail_limit=args.details)
        for _ in range(args.passes)
    ]
    stable = (
        len(runs) < 2
        or (
            runs[0]["api_total_reported"] == runs[1]["api_total_reported"]
            and runs[0]["page_digests"] == runs[1]["page_digests"]
        )
    )
    summary = {
        "contract": "sberbank_ast_bankruptcy_isolated_v2",
        "read_only": True,
        "database_writes": 0,
        "production_changes": 0,
        "passes": len(runs),
        "stable_page_digests": stable,
        "evidence_digest": hashlib.sha256(
            json.dumps(runs, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest(),
        "runs": runs,
        "production_enabled": False,
        "reconciliation_enabled": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if args.passes == 2 and not stable:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
