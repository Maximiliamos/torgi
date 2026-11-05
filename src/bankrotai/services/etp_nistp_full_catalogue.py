"""Complete read-only NISTP active-lot catalogue walk with last-page proof.

The scanner never writes to PostgreSQL, publishes a map, reconciles missing
lots, or archives records.  A successful pass proves only that the selected
filtered ``view_type=lot`` catalogue was walked to its visibly selected final page.
Two matching independent passes are required before the result is stable.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES, SourceSchemaError
from bankrotai.connectors.etp_sources.common import (
    _parse_soup,
    _status,
)
from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILE_BY_ID,
    parse_nistp_lot_observations,
)

MAX_SCAN_PAGES = 10_000
_EXTERNAL_ID = re.compile(r"nistp\.ru:[1-9]\d{0,11}:[1-9]\d{0,4}\Z")
ACTIVE_SOURCE_STATES = ("Торги объявлены", "Прием заявок", "Идут торги")
_ACTIVE_STATE_KEYS = {value.casefold().replace("ё", "е") for value in ACTIVE_SOURCE_STATES}
# Known terminal results may leak through a live filtered catalogue. Do not
# interpret them as active; do not tolerate arbitrary unknown status strings.
_DIAGNOSTIC_TERMINAL_STATE_KEYS = {
    "торги завершены", "торги окончены", "торги не состоялись",
    "прием заявок завершен", "приём заявок завершён",
    "торги отменены",
}
_DIAGNOSTIC_TERMINAL_STATE_KEYS = {
    value.casefold().replace("ё", "е") for value in _DIAGNOSTIC_TERMINAL_STATE_KEYS
}


class NistpCatalogueScanError(ValueError):
    """The observed catalogue does not prove a safe complete page walk."""


async def _search_page_with_retries(
    connector: Any,
    page_number: int,
    *,
    sleep: Callable[[float], None],
    attempts: int = 3,
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return await connector.search({}, cursor=str(page_number))
        except requests.RequestException:
            if attempt >= attempts:
                raise
            await asyncio.to_thread(sleep, float(2 ** (attempt - 1)))
    raise AssertionError("unreachable retry loop")


def _selected_page_is_visible(html: bytes | str, page: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("li.active, .pagination .active"):
        text = " ".join(node.get_text(" ", strip=True).split())
        if text == str(page):
            return True
        if any(
            re.search(rf"pagenum_send\(\s*{page}\s*\)", str(child.get("onclick") or ""))
            for child in node.select("[onclick]")
        ):
            return True
    return False


def _active_filters_are_visible(html: bytes | str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    selected = [
        " ".join(str(node.get("value") or "").split()).casefold().replace("ё", "е")
        for node in soup.select('input[name="trade_state[]"][checked]')
    ]
    return len(selected) == 3 and set(selected) == _ACTIVE_STATE_KEYS


def _is_valid_empty_terminal_page(response: Any, page: int) -> bool:
    """Accept only NISTP's selected, schema-correct empty page without a successor."""
    content = getattr(response, "content", b"")
    if (
        not content
        or not _selected_page_is_visible(content, page)
        or not _active_filters_are_visible(content)
    ):
        return False
    query_pages = parse_qs(urlparse(str(getattr(response, "url", ""))).query).get(
        "pagenum", []
    )
    if query_pages and query_pages != [str(page)]:
        return False
    soup = BeautifulSoup(content, "html.parser")
    headers = " ".join(
        " ".join(node.get_text(" ", strip=True).split()).lower()
        for node in soup.select("th, tr:first-child td")
    )
    required_headers = (
        "код торгов", "организатор", "должник", "начальная цена",
        "начало приема заявок", "конец приема заявок", "состояние",
    )
    if not all(header in headers for header in required_headers):
        return False
    if any(len(row.find_all("td", recursive=False)) == 7 for row in soup.select("tr")[1:]):
        return False
    next_page = page + 1
    for node in soup.select("a, [onclick]"):
        text = " ".join(node.get_text(" ", strip=True).split())
        onclick = str(node.get("onclick") or "")
        if text == str(next_page) or re.search(
            rf"pagenum_send\(\s*{next_page}\s*\)", onclick
        ):
            return False
    return True


async def scan_complete_nistp_catalogue(
    connector: Any,
    *,
    max_pages: int = MAX_SCAN_PAGES,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    diagnose_out_of_scope: bool = False,
) -> dict[str, Any]:
    """Walk one independent NISTP pass or fail without claiming completion."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_SCAN_PAGES:
        raise NistpCatalogueScanError("Unsafe max_pages")
    if not 0 <= delay_seconds <= 10:
        raise NistpCatalogueScanError("Delay must be 0..10 seconds")

    seen: set[str] = set()
    catalogue_records: list[str] = []
    conservative_status_items = 0
    out_of_scope_statuses: Counter[str] = Counter()
    out_of_scope_samples: list[dict[str, Any]] = []
    out_of_scope_missing_prices = 0
    pages: list[dict[str, Any]] = []
    last_page_proven = False
    scan_started = time.monotonic()

    for page_number in range(1, max_pages + 1):
        if page_number > 1:
            await asyncio.to_thread(sleep, delay_seconds)
        print(f"NISTP requesting page={page_number}...", flush=True)
        try:
            result = await _search_page_with_retries(
                connector, page_number, sleep=sleep,
            )
        except SourceSchemaError:
            response = getattr(connector, "_last_response", None)
            if not _is_valid_empty_terminal_page(response, page_number):
                raise
            pages.append({
                "page": page_number,
                "items": 0,
                "property_items": 0,
                "conservative_status_items": 0,
                "out_of_scope_status_items": 0,
                "response_url": str(getattr(response, "url", "")),
                "ids_sha256": hashlib.sha256(b"").hexdigest(),
                "empty_terminal_page": True,
            })
            last_page_proven = True
            print(
                f"NISTP readonly page={page_number} items=0 terminal=proven "
                f"elapsed={time.monotonic() - scan_started:.1f}s",
                flush=True,
            )
            break
        response = getattr(connector, "_last_response", None)
        if response is None or not getattr(response, "content", b""):
            raise NistpCatalogueScanError("NISTP response evidence is unavailable")
        if not _selected_page_is_visible(response.content, page_number):
            raise NistpCatalogueScanError(
                f"NISTP page {page_number} is not visibly selected"
            )
        if not _active_filters_are_visible(response.content):
            raise NistpCatalogueScanError(
                f"NISTP active trade_state filters are not selected on page {page_number}"
            )

        observations = parse_nistp_lot_observations(
            PROFILE_BY_ID["nistp.ru"], _parse_soup(response.content).decode(), limit=100,
        )
        identities = [item["provider_external_id"] for item in observations]
        if not identities or any(not _EXTERNAL_ID.fullmatch(value) for value in identities):
            raise NistpCatalogueScanError("Invalid or empty individual-lot identity page")
        if len(set(identities)) != len(identities):
            raise NistpCatalogueScanError("Duplicate individual-lot identity within page")
        overlap = seen.intersection(identities)
        if overlap:
            raise NistpCatalogueScanError(
                f"Cross-page duplicate individual-lot identity: {sorted(overlap)[0]}"
            )
        page_conservative_status_items = 0
        page_out_of_scope_status_items = 0
        for item in observations:
            source_status = item["source_status_text"]
            source_status_key = source_status.casefold().replace("ё", "е")
            out_of_scope = source_status_key not in _ACTIVE_STATE_KEYS
            if out_of_scope:
                if not diagnose_out_of_scope or source_status_key not in _DIAGNOSTIC_TERMINAL_STATE_KEYS:
                    raise NistpCatalogueScanError(
                        f"Out-of-scope NISTP status: {item['provider_external_id']} "
                        f"value={source_status!r}"
                    )
                # Record the provider's unexpected row, but never grant active
                # inventory acceptance from a contaminated filtered catalogue.
                out_of_scope_statuses[source_status] += 1
                page_out_of_scope_status_items += 1
                if len(out_of_scope_samples) < 20:
                    out_of_scope_samples.append({
                        "page": page_number,
                        "external_id": item["provider_external_id"],
                        "source_status": source_status,
                    })
            price = item["listing_price_rub"]
            if price is None or price <= 0:
                if not out_of_scope:
                    raise NistpCatalogueScanError(
                        f"Missing individual price: {item['provider_external_id']}"
                    )
                out_of_scope_missing_prices += 1
                price_evidence = "MISSING"
            else:
                price_evidence = f"{price:.2f}"
            if not out_of_scope and _status(source_status) == "unknown":
                page_conservative_status_items += 1
            # Include *all* observed rows in the stability digest. Filtering
            # them out would disguise source changes across independent passes.
            catalogue_records.append(
                f"{item['provider_external_id']}\t{price_evidence}\t"
                f"{source_status}\t{int(item['is_property'])}"
            )
        conservative_status_items += page_conservative_status_items
        property_ids = {
            item["provider_external_id"] for item in observations if item["is_property"]
        }
        if property_ids != {item.external_id for item in result.items}:
            raise NistpCatalogueScanError("Property candidate set differs from catalogue parser")
        seen.update(identities)

        expected_next = str(page_number + 1)
        if result.next_cursor not in {None, expected_next}:
            raise NistpCatalogueScanError("Non-sequential NISTP pagination cursor")
        pages.append({
            "page": page_number,
            "items": len(identities),
            "property_items": len(property_ids),
            "conservative_status_items": page_conservative_status_items,
            "out_of_scope_status_items": page_out_of_scope_status_items,
            "response_url": str(getattr(response, "url", "")),
            "ids_sha256": hashlib.sha256(
                "\n".join(sorted(identities)).encode("ascii")
            ).hexdigest(),
        })
        print(
            f"NISTP readonly page={page_number} items={len(identities)} "
            f"unique={len(seen)} next={result.next_cursor or 'none'} "
            f"elapsed={time.monotonic() - scan_started:.1f}s",
            flush=True,
        )
        if result.next_cursor is None:
            last_page_proven = True
            break
    else:
        raise NistpCatalogueScanError("Page budget exhausted before final page")

    digest = hashlib.sha256("\n".join(sorted(seen)).encode("ascii")).hexdigest()
    records_digest = hashlib.sha256(
        "\n".join(sorted(catalogue_records)).encode("utf-8")
    ).hexdigest()
    return {
        "source": "nistp.ru",
        "mode": "full_readonly_active_lot_catalogue_walk",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "pages_scanned": len(pages),
        "last_page": pages[-1]["page"],
        "last_page_proven": last_page_proven,
        "unique_lot_ids": len(seen),
        "lot_ids_sha256": digest,
        "catalogue_records_sha256": records_digest,
        "pages": pages,
        "view_type": "lot",
        "individual_prices_verified": out_of_scope_missing_prices == 0,
        "individual_statuses_verified": True,
        "source_statuses_recognized": not out_of_scope_statuses,
        "active_filter_proven_on_every_page": True,
        "trade_states": list(ACTIVE_SOURCE_STATES),
        "complete_filtered_inventory": last_page_proven and not out_of_scope_statuses,
        "effective_active_scope_proven": last_page_proven and not out_of_scope_statuses,
        "out_of_scope_status_items": sum(out_of_scope_statuses.values()),
        "out_of_scope_status_counts": dict(sorted(out_of_scope_statuses.items())),
        "out_of_scope_samples": out_of_scope_samples,
        "out_of_scope_missing_prices": out_of_scope_missing_prices,
        "normalized_statuses_complete": conservative_status_items == 0 and not out_of_scope_statuses,
        "conservative_unknown_status_items": conservative_status_items,
        "production_database_writes": 0,
        "map_publication": False,
        "complete_source_inventory": False,
        "stable_point_in_time_snapshot": False,
        "archival_allowed": False,
        "reconcile_missing": False,
    }


async def run_two_independent_passes(
    connector_factory: Callable[[], Any],
    *,
    max_pages: int = MAX_SCAN_PAGES,
    delay_seconds: float = 1.0,
    diagnose_out_of_scope: bool = False,
) -> dict[str, Any]:
    passes = []
    for pass_number in range(1, 3):
        print(f"NISTP independent pass {pass_number}/2", flush=True)
        passes.append(await scan_complete_nistp_catalogue(
            connector_factory(), max_pages=max_pages, delay_seconds=delay_seconds,
            diagnose_out_of_scope=diagnose_out_of_scope,
        ))
    stable = (
        passes[0]["last_page"] == passes[1]["last_page"]
        and passes[0]["unique_lot_ids"] == passes[1]["unique_lot_ids"]
        and passes[0]["lot_ids_sha256"] == passes[1]["lot_ids_sha256"]
        and passes[0]["catalogue_records_sha256"]
        == passes[1]["catalogue_records_sha256"]
    )
    return {
        "source": "nistp.ru",
        "independent_passes": passes,
        "passes_match": stable,
        "last_page_proven_twice": bool(stable) and all(
            item["last_page_proven"] for item in passes
        ),
        "active_filter_proven_twice": bool(stable) and all(
            item["active_filter_proven_on_every_page"] for item in passes
        ),
        "effective_active_scope_proven_twice": bool(stable) and all(
            item["effective_active_scope_proven"] for item in passes
        ),
        "out_of_scope_status_items_by_pass": [
            item["out_of_scope_status_items"] for item in passes
        ],
        "trade_states": list(ACTIVE_SOURCE_STATES),
        "production_database_writes": 0,
        "map_publication": False,
        "archival_allowed": False,
        "reconcile_missing": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=MAX_SCAN_PAGES)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--diagnose-out-of-scope", action="store_true",
                        help="Continue through non-active rows; never certify active scope")
    args = parser.parse_args()
    connector_type = SOURCE_CONNECTOR_TYPES["nistp.ru"]
    report = asyncio.run(run_two_independent_passes(
        connector_type, max_pages=args.max_pages, delay_seconds=args.delay_seconds,
        diagnose_out_of_scope=args.diagnose_out_of_scope,
    ))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report["passes_match"]:
        raise SystemExit("NISTP independent full-catalogue passes differ")
    if not report["effective_active_scope_proven_twice"] and not args.diagnose_out_of_scope:
        raise SystemExit("NISTP selected active scope is not proven")


if __name__ == "__main__":
    main()
