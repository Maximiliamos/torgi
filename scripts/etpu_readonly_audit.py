#!/usr/bin/env python3
"""Read-only source-contract audit for the Ural ETP bankruptcy catalogue.

The probe performs only public GET/POST reads required by the site's own
ASP.NET pagination and individual lot detail pages. It never writes provider or
TORGI data and never enables reconciliation/archival/scheduling.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from typing import Any

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES


SOURCE_ID = "etpu.ru"


def _digest(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


async def _catalogue_pass(max_pages: int) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE_ID]()
    cursor: str | None = None
    page = 1
    ordered_ids: list[str] = []
    record_rows: list[str] = []
    page_sizes: list[int] = []
    terminal_proven = False

    while page <= max_pages:
        result = await connector.search({}, cursor=cursor)
        ids = [lot.external_id for lot in result.items]
        page_sizes.append(len(ids))
        ordered_ids.extend(ids)
        record_rows.extend(
            "|".join(
                (
                    lot.external_id,
                    lot.title or "",
                    "" if lot.start_price is None else f"{lot.start_price:.2f}",
                    lot.auction_status or "",
                )
            )
            for lot in result.items
        )
        if result.next_cursor is None:
            terminal_proven = True
            break
        cursor = result.next_cursor
        page += 1

    unique_ids = set(ordered_ids)
    duplicate_ids = len(ordered_ids) - len(unique_ids)
    return {
        "pages": len(page_sizes),
        "page_sizes": page_sizes,
        "terminal_page": len(page_sizes) if terminal_proven else None,
        "terminal_page_proven": terminal_proven,
        "items": len(ordered_ids),
        "unique_ids": len(unique_ids),
        "duplicate_ids": duplicate_ids,
        "ordered_id_digest": _digest(ordered_ids),
        "sorted_id_digest": _digest(sorted(unique_ids)),
        "record_digest": _digest(record_rows),
        "ids": ordered_ids,
    }


async def _details(ids: list[str], limit: int) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE_ID]()
    selected = ids[: min(limit, len(ids))]
    results: list[dict[str, Any]] = []
    for external_id in selected:
        detail = await connector.fetch_lot(external_id)
        raw = detail.raw_data or {}
        results.append(
            {
                "external_id": external_id,
                "identity_ok": detail.external_id == external_id,
                "start_price_ok": detail.start_price is not None,
                "status_ok": detail.auction_status != "unknown",
                "classifier_ok": bool(raw.get("efrsb_classifier")),
                "category": detail.category,
                "classifier_flags": raw.get("efrsb_classifier_flags") or [],
            }
        )

    count = len(results)
    def rate(field: str) -> float:
        return round(sum(bool(row[field]) for row in results) / count, 4) if count else 0.0

    return {
        "checked": count,
        "identity_success_rate": rate("identity_ok"),
        "price_success_rate": rate("start_price_ok"),
        "status_success_rate": rate("status_ok"),
        "classifier_success_rate": rate("classifier_ok"),
        "categories": dict(Counter(str(row["category"]) for row in results)),
        "results": results,
    }


async def run(max_pages: int, detail_limit: int) -> dict[str, Any]:
    first = await _catalogue_pass(max_pages)
    second = await _catalogue_pass(max_pages)

    passes_match = (
        first["terminal_page_proven"]
        and second["terminal_page_proven"]
        and first["sorted_id_digest"] == second["sorted_id_digest"]
        and first["record_digest"] == second["record_digest"]
    )
    details = await _details(first["ids"], detail_limit)

    return {
        "source": SOURCE_ID,
        "mode": "public_read_only",
        "production_enabled": False,
        "safe_for_archival": False,
        "reconcile_missing": False,
        "pass_1": {key: value for key, value in first.items() if key != "ids"},
        "pass_2": {key: value for key, value in second.items() if key != "ids"},
        "two_pass_contract_match": passes_match,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10_000,
        help="Fail closed if the provider terminal page is not reached before this bound.",
    )
    parser.add_argument("--details", type=int, default=30)
    args = parser.parse_args()
    if args.max_pages < 2:
        parser.error("--max-pages must be >= 2")
    if args.details < 1:
        parser.error("--details must be >= 1")

    import asyncio

    report = asyncio.run(run(args.max_pages, args.details))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    pass1 = report["pass_1"]
    pass2 = report["pass_2"]
    details = report["details"]
    ok = (
        bool(pass1["terminal_page_proven"])
        and bool(pass2["terminal_page_proven"])
        and pass1["duplicate_ids"] == 0
        and pass2["duplicate_ids"] == 0
        and bool(report["two_pass_contract_match"])
        and details["checked"] >= min(30, pass1["unique_ids"])
        and details["identity_success_rate"] == 1.0
        and details["price_success_rate"] == 1.0
        and details["status_success_rate"] == 1.0
        and details["classifier_success_rate"] == 1.0
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
