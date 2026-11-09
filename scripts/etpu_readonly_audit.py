#!/usr/bin/env python3
"""Read-only source-contract audit for the Ural ETP bankruptcy catalogue.

The audit uses only official public ETPU form filters. It traverses every
non-terminal status to the provider's terminal page twice, compares stable
identity/record digests, then validates individual detail contracts. It never
writes provider or TORGI state and never enables reconciliation or archival.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import time
from typing import Any

from bankrotai.connectors.etp_sources.etpu import (
    EtpuClient,
    EtpuStatusFilter,
    NON_TERMINAL_STATUS_FILTERS,
    SOURCE_ID,
    STATUS_FILTER_FIELD,
)


def _digest(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _verify_filter_contract() -> dict[str, Any]:
    contract = EtpuClient().fetch_filter_contract()
    status_select = next(
        (
            item
            for item in contract.get("selects", [])
            if item.get("name") == STATUS_FILTER_FIELD
        ),
        None,
    )
    if not isinstance(status_select, dict):
        raise RuntimeError("etpu_filter_contract_missing_status_select")
    options = {
        str(item.get("value") or ""): str(item.get("text") or "")
        for item in status_select.get("options", [])
        if isinstance(item, dict)
    }
    expected = {item.value: item.text for item in NON_TERMINAL_STATUS_FILTERS}
    if any(options.get(value) != text for value, text in expected.items()):
        raise RuntimeError("etpu_filter_contract_non_terminal_options_changed")
    return {
        "method": contract.get("method"),
        "action": contract.get("action"),
        "status_field": STATUS_FILTER_FIELD,
        "verified_non_terminal_options": expected,
    }


def _status_catalogue_pass(
    status_filter: EtpuStatusFilter,
    max_pages: int,
    delay_seconds: float,
) -> dict[str, Any]:
    client = EtpuClient(status_filter=status_filter)
    page = 1
    ordered_ids: list[str] = []
    record_rows: list[str] = []
    page_sizes: list[int] = []
    terminal_proven = False

    while page <= max_pages:
        result = client.fetch_catalogue_page(page)
        ids = [lot.external_id for lot in result.items]
        page_sizes.append(len(ids))
        ordered_ids.extend(ids)
        record_rows.extend(
            "|".join(
                (
                    status_filter.value,
                    lot.external_id,
                    lot.title or "",
                    "" if lot.start_price is None else f"{lot.start_price:.2f}",
                    str((lot.raw_data or {}).get("source_status_text") or ""),
                )
            )
            for lot in result.items
        )
        if result.next_page is None:
            terminal_proven = True
            break
        page = result.next_page
        if delay_seconds:
            time.sleep(delay_seconds)

    unique_ids = set(ordered_ids)
    return {
        "status_value": status_filter.value,
        "status_text": status_filter.text,
        "pages": len(page_sizes),
        "page_sizes": page_sizes,
        "terminal_page": page if terminal_proven else None,
        "terminal_page_proven": terminal_proven,
        "items": len(ordered_ids),
        "unique_ids": len(unique_ids),
        "duplicate_ids": len(ordered_ids) - len(unique_ids),
        "ordered_id_digest": _digest(ordered_ids),
        "sorted_id_digest": _digest(sorted(unique_ids)),
        "record_digest": _digest(record_rows),
        "ids": ordered_ids,
        "record_rows": record_rows,
    }


def _catalogue_pass(max_pages: int, delay_seconds: float) -> dict[str, Any]:
    statuses: list[dict[str, Any]] = []
    all_ids: list[str] = []
    all_records: list[str] = []
    ids_by_status: dict[str, list[str]] = {}

    for status_filter in NON_TERMINAL_STATUS_FILTERS:
        result = _status_catalogue_pass(status_filter, max_pages, delay_seconds)
        statuses.append(result)
        ids = list(result["ids"])
        ids_by_status[status_filter.value] = ids
        all_ids.extend(ids)
        all_records.extend(result["record_rows"])

    unique_ids = set(all_ids)
    cross_status_duplicates = len(all_ids) - len(unique_ids)
    terminal_proven = all(bool(row["terminal_page_proven"]) for row in statuses)
    return {
        "status_catalogues": [
            {key: value for key, value in row.items() if key not in {"ids", "record_rows"}}
            for row in statuses
        ],
        "terminal_page_proven": terminal_proven,
        "items": len(all_ids),
        "unique_ids": len(unique_ids),
        "cross_status_duplicate_ids": cross_status_duplicates,
        "sorted_id_digest": _digest(sorted(unique_ids)),
        "record_digest": _digest(sorted(all_records)),
        "ids_by_status": ids_by_status,
    }


def _round_robin_detail_ids(ids_by_status: dict[str, list[str]], limit: int) -> list[str]:
    buckets = [
        ids_by_status.get(status_filter.value, [])
        for status_filter in NON_TERMINAL_STATUS_FILTERS
    ]
    selected: list[str] = []
    seen: set[str] = set()
    offset = 0
    while len(selected) < limit:
        added = False
        for bucket in buckets:
            if offset >= len(bucket):
                continue
            external_id = bucket[offset]
            if external_id not in seen:
                seen.add(external_id)
                selected.append(external_id)
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        offset += 1
    return selected


def _details(
    ids_by_status: dict[str, list[str]],
    limit: int,
    delay_seconds: float,
) -> dict[str, Any]:
    selected = _round_robin_detail_ids(ids_by_status, limit)
    client = EtpuClient()
    results: list[dict[str, Any]] = []

    for external_id in selected:
        detail = client.fetch_detail(external_id)
        raw = detail.raw_data or {}
        results.append(
            {
                "external_id": external_id,
                "identity_ok": detail.external_id == external_id,
                "start_price_ok": detail.start_price is not None,
                "status_field_ok": bool(raw.get("source_status_text")),
                "normalized_status": detail.auction_status,
                "deadline_ok": detail.application_deadline is not None,
                "classifier_ok": bool(raw.get("efrsb_classifier")),
                "category": detail.category,
                "classifier_flags": raw.get("efrsb_classifier_flags") or [],
                "cadastre_present": detail.cadastral_number is not None,
                "address_present": detail.address is not None,
                "area_present": detail.area is not None,
            }
        )
        if delay_seconds:
            time.sleep(delay_seconds)

    count = len(results)

    def rate(field: str) -> float:
        return round(sum(bool(row[field]) for row in results) / count, 4) if count else 0.0

    return {
        "checked": count,
        "identity_success_rate": rate("identity_ok"),
        "price_success_rate": rate("start_price_ok"),
        "status_field_success_rate": rate("status_field_ok"),
        "deadline_success_rate": rate("deadline_ok"),
        "classifier_success_rate": rate("classifier_ok"),
        "normalized_statuses": dict(Counter(str(row["normalized_status"]) for row in results)),
        "categories": dict(Counter(str(row["category"]) for row in results)),
        "property_field_presence": {
            "cadastre": sum(bool(row["cadastre_present"]) for row in results),
            "address": sum(bool(row["address_present"]) for row in results),
            "area": sum(bool(row["area_present"]) for row in results),
        },
        "results": results,
    }


def run(max_pages: int, detail_limit: int, delay_seconds: float) -> dict[str, Any]:
    filter_contract = _verify_filter_contract()
    first = _catalogue_pass(max_pages, delay_seconds)
    second = _catalogue_pass(max_pages, delay_seconds)

    passes_match = (
        first["terminal_page_proven"]
        and second["terminal_page_proven"]
        and first["sorted_id_digest"] == second["sorted_id_digest"]
        and first["record_digest"] == second["record_digest"]
        and first["cross_status_duplicate_ids"] == 0
        and second["cross_status_duplicate_ids"] == 0
    )
    details = _details(first["ids_by_status"], detail_limit, delay_seconds)

    return {
        "source": SOURCE_ID,
        "mode": "public_read_only",
        "scope": "official_non_terminal_status_filters",
        "isolated_source_module": "bankrotai.connectors.etp_sources.etpu",
        "production_enabled": False,
        "safe_for_archival": False,
        "reconcile_missing": False,
        "filter_contract": filter_contract,
        "pass_1": {key: value for key, value in first.items() if key != "ids_by_status"},
        "pass_2": {key: value for key, value in second.items() if key != "ids_by_status"},
        "two_pass_contract_match": passes_match,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1_000,
        help="Per-status fail-closed bound; success still requires provider terminal page.",
    )
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=0.20)
    args = parser.parse_args()
    if args.max_pages < 2:
        parser.error("--max-pages must be >= 2")
    if args.details < 1:
        parser.error("--details must be >= 1")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be >= 0")

    report = run(args.max_pages, args.details, args.delay_seconds)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    pass1 = report["pass_1"]
    pass2 = report["pass_2"]
    details = report["details"]
    expected_details = min(30, int(pass1["unique_ids"]))
    ok = (
        bool(pass1["terminal_page_proven"])
        and bool(pass2["terminal_page_proven"])
        and pass1["cross_status_duplicate_ids"] == 0
        and pass2["cross_status_duplicate_ids"] == 0
        and bool(report["two_pass_contract_match"])
        and details["checked"] >= expected_details
        and details["identity_success_rate"] == 1.0
        and details["price_success_rate"] == 1.0
        and details["status_field_success_rate"] == 1.0
        and details["deadline_success_rate"] == 1.0
        and details["classifier_success_rate"] == 1.0
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
