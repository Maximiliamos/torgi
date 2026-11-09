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
        "lots": list(lots_by_id.values()),
    }


async def verify_details(
    connector: Any,
    lots: list[Any],
    *,
    detail_limit: int = 30,
) -> dict[str, Any]:
    """Verify individual ARBbitLot cards without converting failures into guessed data."""
    samples: list[dict[str, Any]] = []
    by_procedure: dict[str, list[str]] = defaultdict(list)
    for listing in lots[:detail_limit]:
        item: dict[str, Any] = {"external_id": listing.external_id}
        try:
            detail = await connector.fetch_lot(listing.external_id)
            item.update({
                "detail_ok": True,
                "start_price_ok": detail.start_price is not None,
                "current_price_ok": detail.current_price is not None,
                "status_ok": detail.auction_status != "unknown",
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
                "current_price_ok": False,
                "status_ok": False,
            })
        samples.append(item)

    checked = len(samples)
    detail_ok = sum(bool(item["detail_ok"]) for item in samples)
    start_ok = sum(bool(item["start_price_ok"]) for item in samples)
    status_ok = sum(bool(item["status_ok"]) for item in samples)
    public_offer_samples = [
        item for item in samples if ":public-offers-" in str(item["external_id"])
    ]
    current_ok = sum(bool(item["current_price_ok"]) for item in public_offer_samples)
    multi_lot_procedures = {
        procedure: sorted(set(identities))
        for procedure, identities in by_procedure.items()
        if len(set(identities)) > 1
    }
    return {
        "details_checked": checked,
        "detail_success": detail_ok,
        "start_price_success": start_ok,
        "status_success": status_ok,
        "public_offer_details_checked": len(public_offer_samples),
        "current_price_success": current_ok,
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
    same_ids = (
        first["id_digest_sha256"] == second["id_digest_sha256"]
        and first["unique_ids"] == second["unique_ids"]
    )

    for result in (first, second):
        result.pop("lots", None)
    return {
        "source": SOURCE_ID,
        "read_only": True,
        "production_enabled": False,
        "pass_1": first,
        "pass_2": second,
        "passes_have_identical_ids": same_ids,
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
