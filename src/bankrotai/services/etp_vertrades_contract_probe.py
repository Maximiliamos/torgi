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

from bankrotai.connectors.etp_sources import PROFILES, parse_listing_html
from bankrotai.connectors.etp_sources.pagination import has_next_page, page_url

_PROFILE = next(profile for profile in PROFILES if profile.source_id == "vertrades.ru")
_MAX_RESPONSE_BYTES = 2_000_000
_TIMEOUT = (5, 25)


class VertradesProbeError(RuntimeError):
    pass


def _scan_pass(
    session: requests.Session, *, max_pages: int,
    delay_seconds: float, sleep: Callable[[float], None],
) -> dict:
    seen: set[str] = set()
    pages: list[dict] = []
    terminal_page: int | None = None

    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(delay_seconds)
        url = page_url(_PROFILE, page)
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

        lots = parse_listing_html(_PROFILE, response.content, page=page)
        ids = {lot.external_id for lot in lots}
        if not ids:
            raise VertradesProbeError(f"no_property_lot_ids_on_page:{page}")
        overlap = seen.intersection(ids)
        if overlap:
            raise VertradesProbeError(f"duplicate_lot_ids_across_pages:{page}")
        seen.update(ids)

        has_next = has_next_page(_PROFILE, response.content, page)
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
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
