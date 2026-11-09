from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES


SOURCE_ID = "zakazrf.ru"


def _digest(ids: list[str]) -> str:
    joined = "\n".join(ids).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


async def _catalogue_pass(*, max_pages: int) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE_ID]()
    cursor: str | None = None
    page = 0
    ids: list[str] = []
    urls: dict[str, str] = {}
    page_counts: list[int] = []
    terminal = False

    while page < max_pages:
        result = await connector.search({}, cursor=cursor)
        page += 1
        page_counts.append(len(result.items))
        for lot in result.items:
            if lot.external_id not in urls:
                ids.append(lot.external_id)
                urls[lot.external_id] = lot.lot_url or ""
        cursor = result.next_cursor
        if cursor is None:
            terminal = True
            break

    return {
        "pages": page,
        "terminal_page_proven": terminal,
        "unique_ids": len(ids),
        "duplicate_ids": len(ids) - len(set(ids)),
        "page_counts": page_counts,
        "digest": _digest(ids),
        "ids": ids,
        "urls": urls,
    }


async def _detail_sample(ids: list[str], *, limit: int) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE_ID]()
    checked = 0
    failures: list[dict[str, str]] = []
    price_ok = 0
    current_price_ok = 0
    status_ok = 0
    classifier_ok = 0
    category_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for external_id in ids[:limit]:
        try:
            lot = await connector.fetch_lot(external_id)
        except Exception as exc:  # diagnostic must preserve fail-closed evidence
            failures.append({
                "external_id": external_id,
                "error_type": type(exc).__name__,
            })
            continue
        checked += 1
        price_ok += int(lot.start_price is not None)
        current_price_ok += int(lot.current_price is not None)
        status_ok += int(lot.auction_status != "unknown")
        classifier = str((lot.raw_data or {}).get("structured_property_classifier") or "")
        classifier_ok += int(bool(classifier))
        category_counts[lot.category] += 1
        samples.append({
            "external_id": lot.external_id,
            "category": lot.category,
            "start_price": lot.start_price,
            "current_price": lot.current_price,
            "auction_status": lot.auction_status,
            "cadastral_number": lot.cadastral_number,
            "area": lot.area,
            "address_present": bool(lot.address),
            "classifier": classifier or None,
            "lot_number": (lot.raw_data or {}).get("lot_number"),
        })

    return {
        "requested": min(limit, len(ids)),
        "checked": checked,
        "failures": failures,
        "start_price_success": price_ok,
        "current_price_present": current_price_ok,
        "status_success": status_ok,
        "structured_classifier_success": classifier_ok,
        "category_counts": dict(category_counts),
        "samples": samples,
    }


async def run(*, max_pages: int, detail_limit: int) -> dict[str, Any]:
    first = await _catalogue_pass(max_pages=max_pages)
    second = await _catalogue_pass(max_pages=max_pages)
    comparable = (
        first["terminal_page_proven"]
        and second["terminal_page_proven"]
        and first["ids"] == second["ids"]
    )
    details = await _detail_sample(first["ids"], limit=detail_limit)
    for item in (first, second):
        item.pop("urls", None)
    return {
        "source": SOURCE_ID,
        "read_only": True,
        "production_enabled": False,
        "pass_1": first,
        "pass_2": second,
        "two_pass_ids_equal": comparable,
        "detail": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only ZakazRF catalogue/detail source-contract probe."
    )
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 5000:
        parser.error("--max-pages must be in 1..5000")
    if not 1 <= args.detail_limit <= 200:
        parser.error("--detail-limit must be in 1..200")

    payload = asyncio.run(run(max_pages=args.max_pages, detail_limit=args.detail_limit))
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if (
        payload["pass_1"]["terminal_page_proven"]
        and payload["pass_2"]["terminal_page_proven"]
        and payload["two_pass_ids_equal"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
