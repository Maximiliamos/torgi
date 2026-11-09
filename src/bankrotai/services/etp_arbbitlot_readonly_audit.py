from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Callable

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES


SOURCE_ID = "arbbitlot.ru"


def _digest(ids: list[str]) -> str:
    payload = "\n".join(sorted(ids)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def scan_catalogue(connector: Any, *, max_pages: int = 1000) -> dict[str, Any]:
    """Read ARBbitLot catalogue pages until the provider exposes no next page."""
    cursor: str | None = None
    page_count = 0
    ids: list[str] = []
    lots_by_id: dict[str, Any] = {}
    terminal_page_proven = False

    while page_count < max_pages:
        page = await connector.search({}, cursor=cursor)
        page_count += 1
        for lot in page.items:
            ids.append(lot.external_id)
            lots_by_id.setdefault(lot.external_id, lot)
        if page.next_cursor is None:
            terminal_page_proven = True
            break
        cursor = page.next_cursor

    if not terminal_page_proven:
        raise RuntimeError(f"{SOURCE_ID}: max_pages reached before terminal page")
    duplicates = [identity for identity, count in Counter(ids).items() if count > 1]
    return {
        "page_count": page_count,
        "terminal_page_proven": terminal_page_proven,
        "observed_rows": len(ids),
        "unique_ids": len(lots_by_id),
        "duplicate_ids": duplicates,
        "id_digest_sha256": _digest(list(lots_by_id)),
        "_ids": sorted(lots_by_id),
        "lots": list(lots_by_id.values()),
    }


async def verify_details(
    connector: Any,
    lots: list[Any],
    *,
    detail_limit: int = 30,
) -> dict[str, Any]:
    """Verify individual cards and compare catalogue/detail fields per lot identity."""
    # Reserve part of the sample for public offers so current-price evidence is
    # exercised even when the all-trades catalogue is dominated by auctions.
    public_offers = [lot for lot in lots if ":public-offers-" in lot.external_id]
    others = [lot for lot in lots if ":public-offers-" not in lot.external_id]
    public_offer_target = min(len(public_offers), max(1, detail_limit // 3))
    selected = public_offers[:public_offer_target]
    selected_ids = {lot.external_id for lot in selected}
    for lot in others + public_offers[public_offer_target:]:
        if len(selected) >= detail_limit:
            break
        if lot.external_id not in selected_ids:
            selected.append(lot)
            selected_ids.add(lot.external_id)

    samples: list[dict[str, Any]] = []
    by_procedure: dict[str, list[str]] = defaultdict(list)
    for listing in selected:
        item: dict[str, Any] = {
            "external_id": listing.external_id,
            "catalogue_start_price": listing.start_price,
            "catalogue_current_price": listing.current_price,
            "catalogue_status": listing.auction_status,
        }
        try:
            detail = await connector.fetch_lot(listing.external_id)
            listing_raw = listing.raw_data or {}
            detail_raw = detail.raw_data or {}
            procedure_match = (
                listing_raw.get("procedure_id") is not None
                and detail_raw.get("procedure_id") is not None
                and str(listing_raw.get("procedure_id")) == str(detail_raw.get("procedure_id"))
            )
            lot_number_match = (
                listing_raw.get("lot_number") is not None
                and detail_raw.get("lot_number") is not None
                and str(listing_raw.get("lot_number")) == str(detail_raw.get("lot_number"))
            )
            start_match = (
                listing.start_price is not None
                and detail.start_price is not None
                and abs(float(listing.start_price) - float(detail.start_price)) <= 0.01
            )
            status_match = (
                listing.auction_status != "unknown"
                and detail.auction_status != "unknown"
                and listing.auction_status == detail.auction_status
            )
            detail_current_matches_catalogue = (
                listing.current_price is None
                or detail.current_price is None
                or abs(float(listing.current_price) - float(detail.current_price)) <= 0.01
            )
            item.update({
                "detail_ok": True,
                "start_price_ok": detail.start_price is not None,
                "catalogue_detail_procedure_match": procedure_match,
                "catalogue_detail_lot_number_match": lot_number_match,
                "catalogue_detail_start_price_match": start_match,
                "catalogue_current_price_ok": listing.current_price is not None,
                "detail_current_price_ok": detail.current_price is not None,
                "catalogue_detail_current_price_match": detail_current_matches_catalogue,
                "status_ok": detail.auction_status != "unknown",
                "catalogue_detail_status_match": status_match,
                "procedure_id": detail.procedure_number,
                "lot_number": (detail.raw_data or {}).get("lot_number"),
                "cadastral_number_ok": detail.cadastral_number is not None,
                "area_ok": detail.area is not None,
                "address_ok": detail.address is not None,
            })
            if detail.procedure_number:
                by_procedure[str(detail.procedure_number)].append(detail.external_id)
        except Exception as exc:  # evidence probe: preserve fail-closed reason
            item.update({
                "detail_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "start_price_ok": False,
                "catalogue_detail_procedure_match": False,
                "catalogue_detail_lot_number_match": False,
                "catalogue_detail_start_price_match": False,
                "catalogue_current_price_ok": listing.current_price is not None,
                "detail_current_price_ok": False,
                "catalogue_detail_current_price_match": False,
                "status_ok": False,
                "catalogue_detail_status_match": False,
            })
        samples.append(item)

    checked = len(samples)
    detail_ok = sum(bool(item["detail_ok"]) for item in samples)
    start_ok = sum(bool(item["start_price_ok"]) for item in samples)
    procedure_match = sum(bool(item["catalogue_detail_procedure_match"]) for item in samples)
    lot_number_match = sum(bool(item["catalogue_detail_lot_number_match"]) for item in samples)
    start_match = sum(bool(item["catalogue_detail_start_price_match"]) for item in samples)
    status_ok = sum(bool(item["status_ok"]) for item in samples)
    status_match = sum(bool(item["catalogue_detail_status_match"]) for item in samples)
    public_offer_samples = [
        item for item in samples if ":public-offers-" in str(item["external_id"])
    ]
    catalogue_current_ok = sum(
        bool(item["catalogue_current_price_ok"]) for item in public_offer_samples
    )
    detail_current_ok = sum(
        bool(item["detail_current_price_ok"]) for item in public_offer_samples
    )
    current_match = sum(
        bool(item["catalogue_detail_current_price_match"]) for item in public_offer_samples
    )
    multi_lot_procedures = {
        procedure: sorted(set(identities))
        for procedure, identities in by_procedure.items()
        if len(set(identities)) > 1
    }
    return {
        "details_checked": checked,
        "detail_success": detail_ok,
        "start_price_success": start_ok,
        "catalogue_detail_procedure_match": procedure_match,
        "catalogue_detail_lot_number_match": lot_number_match,
        "catalogue_detail_start_price_match": start_match,
        "status_success": status_ok,
        "catalogue_detail_status_match": status_match,
        "public_offer_details_checked": len(public_offer_samples),
        "catalogue_current_price_success": catalogue_current_ok,
        "detail_current_price_success": detail_current_ok,
        "catalogue_detail_current_price_match": current_match,
        "multi_lot_procedures": multi_lot_procedures,
        "samples": samples,
    }


async def run_audit(
    connector_factory: Callable[[], Any],
    *,
    detail_limit: int = 30,
    max_pages: int = 1000,
) -> dict[str, Any]:
    first_connector = connector_factory()
    first = await scan_catalogue(first_connector, max_pages=max_pages)
    details = await verify_details(first_connector, first["lots"], detail_limit=detail_limit)

    second_connector = connector_factory()
    second = await scan_catalogue(second_connector, max_pages=max_pages)

    first_ids = set(first["_ids"])
    second_ids = set(second["_ids"])
    added = sorted(second_ids - first_ids)
    removed = sorted(first_ids - second_ids)
    same_ids = (
        first["id_digest_sha256"] == second["id_digest_sha256"]
        and first["unique_ids"] == second["unique_ids"]
    )
    pass_comparison = {
        "identical_ids": same_ids,
        "added_count": len(added),
        "removed_count": len(removed),
        "added_ids_sample": added[:20],
        "removed_ids_sample": removed[:20],
    }

    for result in (first, second):
        result.pop("lots", None)
        result.pop("_ids", None)
    return {
        "source": SOURCE_ID,
        "read_only": True,
        "production_enabled": False,
        "pass_1": first,
        "pass_2": second,
        "passes_have_identical_ids": same_ids,
        "pass_comparison": pass_comparison,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.detail_limit <= 100:
        raise SystemExit("--detail-limit must be in 1..100")
    if not 2 <= args.max_pages <= 5000:
        raise SystemExit("--max-pages must be in 2..5000")

    connector_type = SOURCE_CONNECTOR_TYPES[SOURCE_ID]
    report = asyncio.run(
        run_audit(
            connector_type,
            detail_limit=args.detail_limit,
            max_pages=args.max_pages,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
