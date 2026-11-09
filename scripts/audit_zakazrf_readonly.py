from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from bankrotai.connectors.etp_sources.zakazrf import ZakazRfReadonlyClient


SOURCE_ID = "zakazrf.ru"


def _digest(ids: list[str]) -> str:
    joined = "\n".join(ids).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


async def _catalogue_pass(*, max_pages: int, delay_seconds: float) -> dict[str, Any]:
    connector = ZakazRfReadonlyClient()
    cursor: str | None = None
    page = 0
    ids: list[str] = []
    raw_ids: list[str] = []
    urls: dict[str, str] = {}
    page_counts: list[int] = []
    terminal = False

    while page < max_pages:
        result = await asyncio.to_thread(connector.search_page, page + 1)
        page += 1
        page_counts.append(len(result.items))
        for lot in result.items:
            raw_ids.append(lot.external_id)
            if lot.external_id not in urls:
                ids.append(lot.external_id)
                urls[lot.external_id] = lot.lot_url or ""
        cursor = str(result.next_page) if result.next_page is not None else None
        if cursor is not None and delay_seconds:
            await asyncio.sleep(delay_seconds)
        if cursor is None:
            terminal = True
            break

    return {
        "pages": page,
        "terminal_page_proven": terminal,
        "raw_items": len(raw_ids),
        "unique_ids": len(ids),
        "duplicate_ids": len(raw_ids) - len(set(raw_ids)),
        "page_counts": page_counts,
        "digest": _digest(ids),
        "ids": ids,
        "urls": urls,
    }


async def _detail_sample(ids: list[str], *, limit: int, delay_seconds: float) -> dict[str, Any]:
    connector = SOURCE_CONNECTOR_TYPES[SOURCE_ID]()
    checked = 0
    failures: list[dict[str, str]] = []
    price_ok = 0
    current_price_ok = 0
    status_ok = 0
    classifier_ok = 0
    identity_mismatches: list[dict[str, str]] = []
    category_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for external_id in ids[:limit]:
        try:
            lot = await asyncio.to_thread(connector.fetch_lot, external_id)
        except Exception as exc:  # diagnostic must preserve fail-closed evidence
            failures.append({
                "external_id": external_id,
                "error_type": type(exc).__name__,
            })
            continue
        if lot.external_id != external_id:
            identity_mismatches.append({
                "requested_external_id": external_id,
                "returned_external_id": lot.external_id,
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
        if delay_seconds:
            await asyncio.sleep(delay_seconds)

    return {
        "requested": min(limit, len(ids)),
        "checked": checked,
        "failures": failures,
        "identity_mismatches": identity_mismatches,
        "start_price_success": price_ok,
        "current_price_present": current_price_ok,
        "status_success": status_ok,
        "structured_classifier_success": classifier_ok,
        "category_counts": dict(category_counts),
        "samples": samples,
    }


async def run(*, max_pages: int, detail_limit: int, delay_seconds: float) -> dict[str, Any]:
    first = await _catalogue_pass(max_pages=max_pages, delay_seconds=delay_seconds)
    second = await _catalogue_pass(max_pages=max_pages, delay_seconds=delay_seconds)
    comparable = (
        first["terminal_page_proven"]
        and second["terminal_page_proven"]
        and first["ids"] == second["ids"]
    )
    first_ids = first["ids"]
    second_ids = second["ids"]
    details = await _detail_sample(first_ids, limit=detail_limit, delay_seconds=delay_seconds)
    first_set, second_set = set(first_ids), set(second_ids)
    comparison = {
        "same_ordered_ids": first_ids == second_ids,
        "same_id_set": first_set == second_set,
        "only_in_pass_1": sorted(first_set - second_set)[:20],
        "only_in_pass_2": sorted(second_set - first_set)[:20],
    }
    for item in (first, second):
        item.pop("urls", None)
        item.pop("ids", None)
    return {
        "source": SOURCE_ID,
        "read_only": True,
        "production_enabled": False,
        "pass_1": first,
        "pass_2": second,
        "two_pass_ids_equal": comparable,
        "comparison": comparison,
        "detail": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only ZakazRF catalogue/detail source-contract probe."
    )
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 5000:
        parser.error("--max-pages must be in 1..5000")
    if not 1 <= args.detail_limit <= 200:
        parser.error("--detail-limit must be in 1..200")
    if not 0 <= args.delay_seconds <= 10:
        parser.error("--delay-seconds must be in 0..10")

    payload = asyncio.run(run(
        max_pages=args.max_pages,
        detail_limit=args.detail_limit,
        delay_seconds=args.delay_seconds,
    ))
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    expected_details = min(args.detail_limit, payload["pass_1"]["unique_ids"])
    return 0 if (
        payload["pass_1"]["terminal_page_proven"]
        and payload["pass_2"]["terminal_page_proven"]
        and payload["two_pass_ids_equal"]
        and payload["pass_1"]["duplicate_ids"] == 0
        and payload["pass_2"]["duplicate_ids"] == 0
        and payload["detail"]["checked"] == expected_details
        and not payload["detail"]["identity_mismatches"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
