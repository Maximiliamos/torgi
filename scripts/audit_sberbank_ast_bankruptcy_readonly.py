"""Read-only Sberbank-AST bankruptcy catalogue contract probe.

This probe never persists lots and never enables reconciliation.  It verifies the
provider-returned bankruptcy markers before accepting any property row.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from dataclasses import asdict
from typing import Any

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES, SourceSchemaError

SOURCE = "sberbank-ast.ru"
PAGE_SIZE = 21
DISPLAY_CAP = 10_000


def _lot_evidence(lot: Any) -> dict[str, Any]:
    raw = lot.raw_data or {}
    return {
        "external_id": lot.external_id,
        "procedure_number": lot.procedure_number,
        "category": lot.category,
        "price_present": lot.start_price is not None or lot.current_price is not None,
        "status": lot.auction_status,
        "application_deadline_present": lot.application_deadline is not None,
        "auction_at_present": lot.auction_at is not None,
        "address_present": bool(lot.address),
        "cadastral_number_present": bool(lot.cadastral_number),
        "area_present": lot.area is not None,
        "bankruptcy_filter_verified": raw.get("bankruptcy_filter_verified") is True,
        "property_scope_verified": raw.get("property_scope_verified") is True,
        "individual_lot_identity_verified": raw.get("individual_lot_identity_verified") is True,
    }


async def _walk(*, full: bool, detail_limit: int) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE]()
    first = await connector.search({})
    total = int(first.metadata.get("api_total") or 0)
    if total <= 0:
        raise SourceSchemaError(f"{SOURCE}: API total is not positive")

    completeness = "NOT_PROVEN"
    terminal_page = None
    if total < DISPLAY_CAP:
        terminal_page = max(1, math.ceil(total / PAGE_SIZE))
        completeness = "PROVEN_BY_TOTAL_CONTRACT"
    pages = [1]
    if total > PAGE_SIZE:
        pages.append(2)
    if terminal_page and terminal_page not in pages:
        pages.append(terminal_page)
    if full and terminal_page:
        pages = list(range(1, terminal_page + 1))

    page_results: dict[int, Any] = {1: first}
    for page in pages:
        if page == 1:
            continue
        page_results[page] = await connector.search({}, cursor=str(page))

    ids: list[str] = []
    page_digests: dict[str, str] = {}
    details: list[dict[str, Any]] = []
    for page in pages:
        result = page_results[page]
        page_ids = [lot.external_id for lot in result.items]
        ids.extend(page_ids)
        page_digests[str(page)] = hashlib.sha256(
            "\n".join(page_ids).encode("utf-8")
        ).hexdigest()
        for lot in result.items:
            if len(details) >= detail_limit:
                break
            detail = await connector.fetch_lot(lot.external_id)
            details.append(_lot_evidence(detail))

    if len(ids) != len(set(ids)):
        raise SourceSchemaError(f"{SOURCE}: duplicate accepted lot identities in probed pages")

    return {
        "source": SOURCE,
        "api_total": total,
        "display_cap": DISPLAY_CAP,
        "source_completeness": completeness,
        "terminal_page": terminal_page,
        "pages_checked": pages,
        "page_digests": page_digests,
        "unique_accepted_property_ids": len(set(ids)),
        "details_checked": len(details),
        "details": details,
        "production_enabled": False,
        "reconciliation_enabled": False,
    }


async def _main(args: argparse.Namespace) -> int:
    runs = []
    for _ in range(args.passes):
        runs.append(await _walk(full=args.full, detail_limit=args.details))

    stable = len(runs) < 2 or runs[0]["page_digests"] == runs[1]["page_digests"]
    output = {
        "contract": "sberbank_ast_bankruptcy_readonly_v1",
        "passes": len(runs),
        "stable_page_digests": stable,
        "runs": runs,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    if args.passes >= 2 and not stable:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, choices=(1, 2), default=2)
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument(
        "--full",
        action="store_true",
        help="walk every page only when API Total is below the 10,000 display cap",
    )
    args = parser.parse_args()
    if not 0 <= args.details <= 100:
        parser.error("--details must be between 0 and 100")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
