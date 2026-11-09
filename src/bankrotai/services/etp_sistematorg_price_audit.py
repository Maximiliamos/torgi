"""Read-only full-catalogue auditor for Sistematorg.

Counts procedures and individual lots separately, proves provider terminal
pagination, verifies selected detail pages, and performs no database writes.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter

from bankrotai.connectors.etp_sources.sistematorg_contract import (
    PROPERTY_CATEGORIES,
    SistematorgReadonlyClient,
    parse_sistematorg_catalogue_page,
    parse_sistematorg_detail_lots,
)


async def audit_sistematorg(
    *, max_details: int = 30, max_pages: int = 1000,
    delay_seconds: float = 0.5, passes: int = 2, connector=None,
) -> dict:
    if type(max_details) is not int or not 1 <= max_details <= 100:
        raise ValueError("max_details must be in 1..100")
    if type(max_pages) is not int or not 1 <= max_pages <= 2000:
        raise ValueError("max_pages must be in 1..2000")
    if passes not in {1, 2}:
        raise ValueError("passes must be 1 or 2")
    if not 0 <= delay_seconds <= 10:
        raise ValueError("delay_seconds must be in 0..10")

    source = connector or SistematorgReadonlyClient()
    pass_results: list[dict] = []
    detail_targets: list[tuple[str, str]] = []

    for pass_number in range(1, passes + 1):
        counts: Counter[str] = Counter()
        ids: list[str] = []
        terminal_page = None
        if hasattr(source, "reset"):
            source.reset()
        for page in range(1, max_pages + 1):
            if page > 1 and delay_seconds:
                await asyncio.sleep(delay_seconds)
            response = await asyncio.to_thread(source.fetch_catalogue_page, page)
            observed = parse_sistematorg_catalogue_page(response.content, page=page)
            counts["catalogue_pages"] += 1
            for reason, value in observed.rejects.items():
                counts["reject_" + reason] += value
            for procedure in observed.procedures:
                counts["procedures"] += 1
                if procedure.declared_lot_count == 1:
                    counts["single_lot_procedures"] += 1
                else:
                    counts["multi_lot_procedures"] += 1
                for lot in procedure.lots:
                    counts["individual_lots"] += 1
                    ids.append(lot.external_id)
                    if lot.category in PROPERTY_CATEGORIES:
                        counts["property_lots"] += 1
                        if pass_number == 1 and len(detail_targets) < max_details:
                            detail_targets.append((procedure.detail_url, lot.external_id))
                    else:
                        counts["non_property_lots"] += 1
            if not source.has_next_page(response.content, page):
                terminal_page = page
                break
        if terminal_page is None:
            counts["reject_terminal_not_proven"] += 1
        digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()
        pass_results.append({
            **dict(counts),
            "terminal_page": terminal_page,
            "unique_individual_ids": len(set(ids)),
            "identity_digest": digest,
        })

    details: Counter[str] = Counter()
    detail_ids: set[str] = set()
    for index, (detail_url, expected_external_id) in enumerate(detail_targets):
        if index and delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            response = await asyncio.to_thread(source.fetch_detail, detail_url)
            parsed = parse_sistematorg_detail_lots(
                response.content, detail_url=detail_url,
            )
            matched = [lot for lot in parsed if lot.external_id == expected_external_id]
            if len(matched) != 1:
                details["reject_detail_identity_mismatch"] += 1
                continue
            verified_detail = matched[0]
            detail_ids.add(verified_detail.external_id)
            details["details_verified"] += 1
            if verified_detail.start_price is not None:
                details["prices_verified"] += 1
            else:
                details["reject_missing_initial_price"] += 1
            if verified_detail.auction_status != "unknown":
                details["status_verified"] += 1
            else:
                details["reject_unknown_status"] += 1
            if verified_detail.public_offer_schedule:
                details["public_offer_schedules_verified"] += 1
        except Exception as exc:
            details["reject_detail_" + type(exc).__name__] += 1

    stable = passes == 1 or (
        pass_results[0]["identity_digest"] == pass_results[1]["identity_digest"]
        and pass_results[0]["unique_individual_ids"] == pass_results[1]["unique_individual_ids"]
    )
    return {
        "source": "sistematorg.com",
        "mode": "read_only_full_catalogue_per_lot_audit",
        "passes": pass_results,
        "two_pass_identity_stable": stable if passes == 2 else None,
        "detail_sample_requested": max_details,
        "unique_detail_ids_verified": len(detail_ids),
        **dict(details),
        "production_database_writes": 0,
        "archival_allowed": False,
        "reconciliation_allowed": False,
        "production_enabled": False,
    }


async def audit_price_evidence(
    *, max_details: int = 10, delay_seconds: float = 1.0, connector=None,
) -> dict:
    """Preserve the original bounded single-lot probe API.

    With no connector this uses the isolated Sistematorg client.  A connector
    may still be injected by the legacy zero-write tests.
    """
    if type(max_details) is not int or not 1 <= max_details <= 20:
        raise ValueError("max_details must be in 1..20")
    if not 0.5 <= delay_seconds <= 10:
        raise ValueError("delay_seconds must be in 0.5..10")

    counts: Counter[str] = Counter()

    if connector is not None:
        page = await connector.search({})
        for candidate in page.items:
            counts["catalogue_trade_rows"] += 1
            if candidate.raw_data.get("reported_trade_lots") != 1:
                counts["multiple_or_unknown_lots"] += 1
                continue
            counts["single_lot_trade_rows"] += 1
            if counts["detail_attempts"] >= max_details:
                continue
            if counts["detail_attempts"]:
                await asyncio.sleep(delay_seconds)
            counts["detail_attempts"] += 1
            try:
                detail = await connector.fetch_lot(candidate.external_id)
            except Exception as exc:
                counts["detail_failure_" + type(exc).__name__] += 1
                continue
            if detail.start_price is not None or detail.current_price is not None:
                counts["single_lot_detail_with_labelled_price"] += 1
            else:
                counts["single_lot_detail_missing_labelled_price"] += 1
    else:
        client = SistematorgReadonlyClient()
        response = await asyncio.to_thread(client.fetch_catalogue_page, 1)
        page = parse_sistematorg_catalogue_page(response.content, page=1)
        for procedure in page.procedures:
            counts["catalogue_trade_rows"] += 1
            if procedure.declared_lot_count != 1:
                counts["multiple_or_unknown_lots"] += 1
                continue
            counts["single_lot_trade_rows"] += 1
            if counts["detail_attempts"] >= max_details:
                continue
            if counts["detail_attempts"]:
                await asyncio.sleep(delay_seconds)
            counts["detail_attempts"] += 1
            try:
                detail_response = await asyncio.to_thread(
                    client.fetch_detail, procedure.detail_url
                )
                details = parse_sistematorg_detail_lots(
                    detail_response.content, detail_url=procedure.detail_url,
                )
            except Exception as exc:
                counts["detail_failure_" + type(exc).__name__] += 1
                continue
            if len(details) != 1:
                counts["detail_failure_identity"] += 1
                continue
            detail = details[0]
            if detail.start_price is not None or detail.current_price is not None:
                counts["single_lot_detail_with_labelled_price"] += 1
            else:
                counts["single_lot_detail_missing_labelled_price"] += 1

    return {
        "source": "sistematorg.com",
        "mode": "read_only_first_page_trade_price_evidence",
        **dict(counts),
        "coverage_complete": False,
        "multi_lot_prices_attributed": False,
        "production_database_writes": 0,
        "archival_allowed": False,
        "map_publication": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-details", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--passes", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    report = asyncio.run(audit_sistematorg(
        max_details=args.max_details, max_pages=args.max_pages,
        delay_seconds=args.delay_seconds, passes=args.passes,
    ))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
