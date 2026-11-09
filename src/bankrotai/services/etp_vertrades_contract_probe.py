"""Bounded read-only source-contract probe for arest.vertrades.ru.

Two passes over at most four public catalogue pages compare only composite lot
IDs and a SHA-256 digest.  No debtor/owner data, HTML, cookies or credentials
are retained.  This is evidence tooling only; it cannot enable reconciliation,
archival or production scheduling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from typing import Callable

import requests
from bankrotai.connectors.etp_sources.vertrades import (
    _vertrades_status,
    catalogue_page_url,
    get_vertrades_lot,
    has_next_catalogue_page,
    parse_catalogue_page,
    parse_filter_form_contract,
)
_MAX_RESPONSE_BYTES = 2_000_000
_TIMEOUT = (5, 25)
_DETAIL_PROVIDER_ID = "102288306"
_DETAIL_LOT_NUMBERS = (
    1, 2, 3, 4, 5, 6, 7, 9, 10, 11,
    12, 13, 14, 15, 16, 17, 18, 19, 21, 22,
    23, 24, 25, 26, 27, 28, 29, 33, 34, 35,
)


class VertradesProbeError(RuntimeError):
    pass


def _filter_form_contract(html: str | bytes) -> dict:
    """Compatibility wrapper around the isolated source contract."""
    try:
        return parse_filter_form_contract(html)
    except ValueError as exc:
        raise VertradesProbeError(str(exc)) from exc


def _scan_pass(
    session: requests.Session, *, max_pages: int,
    delay_seconds: float, sleep: Callable[[float], None],
) -> dict:
    seen: set[str] = set()
    pages: list[dict] = []
    terminal_page: int | None = None
    filter_form: dict | None = None

    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(delay_seconds)
        url = catalogue_page_url(page)
        response = session.get(
            url,
            timeout=_TIMEOUT,
            allow_redirects=False,
            headers={
                "User-Agent": "TORGI-Vertrades-readonly-contract-probe/1",
                "Accept": "text/html",
            },
        )
        if response.status_code in {301, 302, 303, 307, 308, 401, 403, 429}:
            raise VertradesProbeError(f"provider_refused_or_redirected:{response.status_code}")
        if response.status_code != 200:
            raise VertradesProbeError(f"unexpected_status:{response.status_code}")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise VertradesProbeError("catalogue_response_too_large")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise VertradesProbeError("catalogue_response_not_html")

        if page == 1:
            filter_form = _filter_form_contract(response.content)

        lots = parse_catalogue_page(response.content, page=page)
        ids = {lot.external_id for lot in lots}
        if not ids:
            raise VertradesProbeError(f"no_property_lot_ids_on_page:{page}")
        overlap = seen.intersection(ids)
        if overlap:
            raise VertradesProbeError(f"duplicate_lot_ids_across_pages:{page}")
        seen.update(ids)

        has_next = has_next_catalogue_page(response.content, page)
        pages.append({
            "page": page,
            "property_lot_ids": len(ids),
            "has_next_page": has_next,
        })
        if not has_next:
            terminal_page = page
            break

    ordered = "\n".join(sorted(seen)).encode("utf-8")
    return {
        "pages": pages,
        "terminal_page": terminal_page,
        "unique_property_lot_ids": len(seen),
        "id_digest_sha256": hashlib.sha256(ordered).hexdigest(),
        "filter_form": filter_form,
    }


def probe_vertrades_detail_sample(
    session: requests.Session, *, provider_id: str = _DETAIL_PROVIDER_ID,
    lot_numbers: tuple[int, ...] = _DETAIL_LOT_NUMBERS,
) -> dict:
    """Verify >=30 exact lots from one shared public procedure response."""
    if len(lot_numbers) < 30 or len(set(lot_numbers)) != len(lot_numbers):
        raise ValueError("Vertrades live detail proof requires at least 30 unique lot numbers")
    if not provider_id.isdigit():
        raise ValueError("Vertrades provider detail identity must be numeric")

    url = f"https://arest.vertrades.ru/showTrades?id={provider_id}"
    response = session.get(
        url,
        timeout=_TIMEOUT,
        allow_redirects=False,
        headers={
            "User-Agent": "TORGI-Vertrades-readonly-contract-probe/1",
            "Accept": "text/html",
        },
    )
    if response.status_code in {301, 302, 303, 307, 308, 401, 403, 429}:
        raise VertradesProbeError(f"detail_provider_refused_or_redirected:{response.status_code}")
    if response.status_code != 200:
        raise VertradesProbeError(f"detail_unexpected_status:{response.status_code}")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise VertradesProbeError("detail_response_too_large")
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise VertradesProbeError("detail_response_not_html")

    fingerprints: list[str] = []
    price_verified = 0
    status_verified = 0
    cadastre_present = 0
    address_present = 0
    area_present = 0
    dates_verified = 0
    procedure_numbers: set[str] = set()

    for lot_number in lot_numbers:
        try:
            section = get_vertrades_lot(
                response.content,
                provider_id=provider_id,
                lot_number=lot_number,
            )
        except ValueError as exc:
            raise VertradesProbeError(
                f"detail_lot_{lot_number}_failed:{exc}"
            ) from exc
        normalized_status = _vertrades_status(section.status_text)
        if normalized_status == "unknown":
            raise VertradesProbeError(f"detail_lot_{lot_number}_status_unrecognized")
        if section.initial_price_rub <= 0:
            raise VertradesProbeError(f"detail_lot_{lot_number}_price_invalid")

        price_verified += 1
        status_verified += 1
        cadastre_present += int(section.cadastral_number is not None)
        address_present += int(section.address is not None)
        area_present += int(section.area_sqm is not None)
        dates_verified += int(
            section.application_deadline is not None and section.auction_at is not None
        )
        procedure_numbers.add(section.procedure_number)
        fingerprints.append(
            f"{section.external_id}|{section.initial_price_rub:.2f}|{normalized_status}"
        )

    if len(procedure_numbers) != 1:
        raise VertradesProbeError("detail_sample_procedure_identity_changed")

    digest = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()
    return {
        "probe": "vertrades_public_shared_card_30_lot_contract",
        "provider_id": provider_id,
        "details_checked": len(lot_numbers),
        "unique_lot_numbers": len(set(lot_numbers)),
        "price_verified": price_verified,
        "status_verified": status_verified,
        "cadastre_present": cadastre_present,
        "address_present": address_present,
        "area_present": area_present,
        "dates_verified": dates_verified,
        "detail_digest_sha256": digest,
        "database_writes": 0,
        "reconciliation_allowed": False,
        "archival_allowed": False,
        "production_enabled": False,
    }


def probe_vertrades_contract(
    session: requests.Session, *, max_pages: int = 4,
    passes: int = 2, delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    if not 2 <= passes <= 2:
        raise ValueError("Vertrades contract proof requires exactly two passes")
    if not 2 <= max_pages <= 4:
        raise ValueError("Vertrades probe accepts 2..4 pages")
    if not 0.5 <= delay_seconds <= 10:
        raise ValueError("Vertrades probe delay must be 0.5..10 seconds")

    results = []
    for pass_number in range(1, passes + 1):
        if pass_number > 1:
            sleep(delay_seconds)
        results.append(_scan_pass(
            session, max_pages=max_pages, delay_seconds=delay_seconds, sleep=sleep,
        ))

    first, second = results
    stable = (
        first["terminal_page"] == second["terminal_page"]
        and first["unique_property_lot_ids"] == second["unique_property_lot_ids"]
        and first["id_digest_sha256"] == second["id_digest_sha256"]
        and first["filter_form"] == second["filter_form"]
    )
    if not stable:
        raise VertradesProbeError("catalogue_identity_digest_changed_between_passes")

    return {
        "probe": "vertrades_public_catalogue_two_pass_contract",
        "passes": passes,
        "terminal_page": first["terminal_page"],
        "terminal_page_proven": first["terminal_page"] is not None,
        "pages_per_pass": len(first["pages"]),
        "unique_property_lot_ids": first["unique_property_lot_ids"],
        "id_digest_sha256": first["id_digest_sha256"],
        "stable_between_passes": True,
        "pages": first["pages"],
        "filter_form": first["filter_form"],
        "complete_source_inventory": False,
        "database_writes": 0,
        "reconciliation_allowed": False,
        "archival_allowed": False,
        "production_enabled": False,
    }



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    with requests.Session() as session:
        report = probe_vertrades_contract(
            session,
            max_pages=args.max_pages,
            passes=args.passes,
            delay_seconds=args.delay_seconds,
        )
        detail = probe_vertrades_detail_sample(session)
    print(json.dumps(
        {"catalogue": report, "detail_sample": detail},
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
