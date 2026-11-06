"""Complete two-pass read-only walk of the selected public M-ETS catalogue.

The scanner proves only the exact seven-category, announced/application-intake
filter encoded by :data:`APARTMENTS_URL`.  It never writes PostgreSQL, performs
missing-lot reconciliation, archives records, geocodes lots or publishes maps.
The selected category set deliberately does not include standalone land (11)
or agricultural real estate (40), so even a successful result is not a claim
of complete source inventory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.registry.mets_public import (
    APARTMENTS_URL,
    BASE,
    DETAIL_PATH,
    MAX_HTML_BYTES,
    MetsEvidenceError,
    discover_lot_urls,
)

MAX_SCAN_PAGES = 500
REQUEST_TIMEOUT = (5, 25)
SELECTED_CATEGORY_IDS = ("36", "34", "39", "37", "38", "28", "41")
SELECTED_STATUS_IDS = ("0", "1")
SELECTED_SOURCE_STATUSES = (
    "Объявленные торги",
    "Торги в стадии приема заявок",
)
OMITTED_PROPERTY_CATEGORY_IDS = ("11", "40")
_REGISTRATION = re.compile(
    r"(?P<trade>\d{4,9})-МЭТС\s*,\s*Лот\s*№\s*(?P<lot>\d{1,5})"
)


class MetsCatalogueScanError(MetsEvidenceError):
    """Observed HTML does not prove a complete, stable selected catalogue."""


def _page_url(page_number: int) -> str:
    return APARTMENTS_URL if page_number == 1 else f"{APARTMENTS_URL}&page={page_number}"


def _selected_values(soup: BeautifulSoup, name: str) -> tuple[str, ...]:
    select = soup.select_one(f'select[name="{name}"]')
    if select is None:
        raise MetsCatalogueScanError(f"M-ETS {name} filter is absent")
    return tuple(
        str(option.get("value") or "")
        for option in select.select("option[selected]")
    )


def _verify_selected_filters(soup: BeautifulSoup) -> None:
    categories = _selected_values(soup, "search_category")
    statuses = _selected_values(soup, "stat")
    if len(categories) != len(SELECTED_CATEGORY_IDS) or set(categories) != set(
        SELECTED_CATEGORY_IDS
    ):
        raise MetsCatalogueScanError(
            "M-ETS selected category filter differs from the encoded catalogue scope"
        )
    if len(statuses) != len(SELECTED_STATUS_IDS) or set(statuses) != set(
        SELECTED_STATUS_IDS
    ):
        raise MetsCatalogueScanError(
            "M-ETS selected status filter is not announced plus application intake"
        )


def _parse_digits(value: str, *, field: str) -> int:
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        raise MetsCatalogueScanError(f"M-ETS {field} is not numeric")
    parsed = int(digits)
    if parsed <= 0:
        raise MetsCatalogueScanError(f"M-ETS {field} must be positive")
    return parsed


def _reported_total(soup: BeautifulSoup) -> int:
    visible = soup.select_one(".search-results-info .value")
    machine = soup.select_one('meta[itemprop="offerCount"][content]')
    if visible is None or machine is None:
        raise MetsCatalogueScanError("M-ETS reported total evidence is incomplete")
    visible_total = _parse_digits(visible.get_text(" ", strip=True), field="visible total")
    machine_total = _parse_digits(str(machine.get("content") or ""), field="offerCount")
    if visible_total != machine_total:
        raise MetsCatalogueScanError("M-ETS visible total contradicts offerCount")
    return visible_total


def _selected_page_is_visible(soup: BeautifulSoup, page_number: int) -> bool:
    paginators = soup.select(".paginator")
    if not paginators:
        return False
    for paginator in paginators:
        active = paginator.select(".active")
        if (
            len(active) != 1
            or active[0].get_text(" ", strip=True) != str(page_number)
        ):
            return False
    return True


def _paginator_marker_summary(soup: BeautifulSoup) -> str:
    """Return bounded, non-lot structural evidence for parser failures."""
    active = soup.select(".paginator .active")
    markers = [
        {
            "tag": marker.name,
            "classes": sorted(str(value) for value in marker.get("class", [])),
            "text": marker.get_text(" ", strip=True)[:40],
        }
        for marker in active[:5]
    ]
    return json.dumps(
        {
            "paginator_count": len(soup.select(".paginator")),
            "active_count": len(active),
            "active_markers": markers,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _next_page(soup: BeautifulSoup, page_number: int) -> int | None:
    paginators = soup.select("div.paginator")
    if not paginators:
        raise MetsCatalogueScanError("M-ETS paginator is absent")
    links_by_paginator = [
        paginator.select('a.next.link[aria-label="Следующая страница"]')
        for paginator in paginators
    ]
    if any(len(links) > 1 for links in links_by_paginator):
        raise MetsCatalogueScanError("M-ETS paginator has multiple local next controls")
    present = [links[0] for links in links_by_paginator if links]
    if not present:
        return None
    if len(present) != len(paginators):
        raise MetsCatalogueScanError("M-ETS paginator next controls disagree")
    hrefs = {urljoin(BASE, str(link.get("href") or "")) for link in present}
    if len(hrefs) != 1:
        raise MetsCatalogueScanError("M-ETS paginator next targets disagree")
    href = hrefs.pop()
    parsed = urlsplit(href)
    expected_query = parse_qs(urlsplit(APARTMENTS_URL).query)
    actual_query = parse_qs(parsed.query)
    next_values = actual_query.pop("page", [])
    if (
        parsed.scheme != "https"
        or parsed.netloc != "m-ets.ru"
        or parsed.path != "/search"
        or actual_query != expected_query
        or next_values != [str(page_number + 1)]
    ):
        raise MetsCatalogueScanError(
            "M-ETS next control does not preserve the selected filter and sequential page"
        )
    return page_number + 1


def _normal_price(content: str) -> str:
    try:
        value = Decimal(content)
    except InvalidOperation as exc:
        raise MetsCatalogueScanError("M-ETS catalogue price is not decimal") from exc
    if not value.is_finite() or value <= 0:
        raise MetsCatalogueScanError("M-ETS catalogue price must be finite and positive")
    return format(value.normalize(), "f")


def _card_observation(card: Tag) -> tuple[str, str, str, str]:
    links = card.select("a.search-comp-item[href]")
    if len(links) != 1:
        raise MetsCatalogueScanError("M-ETS card lacks one canonical detail link")
    href = urljoin(BASE, str(links[0].get("href") or ""))
    parsed = urlsplit(href)
    match = DETAIL_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "m-ets.ru"
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise MetsCatalogueScanError("M-ETS card detail link is not canonical")
    trade, lot = match.groups()
    external_id = f"{trade}-{lot}"

    registration_nodes = card.select(".comp-info-description .comp-regnumber")
    if len(registration_nodes) != 1:
        raise MetsCatalogueScanError("M-ETS card registration identity is ambiguous")
    registration = _REGISTRATION.search(
        registration_nodes[0].get_text(" ", strip=True)
    )
    if registration is None or registration.groups() != (trade, lot):
        raise MetsCatalogueScanError("M-ETS card registration contradicts detail URL")

    title_node = card.select_one('.comp-title[itemprop="name"]')
    title = " ".join(title_node.get_text(" ", strip=True).split()) if title_node else ""
    if not title:
        raise MetsCatalogueScanError("M-ETS card title is absent")

    status_node = card.select_one(".comp-type")
    status_text = " ".join(
        status_node.get_text(" ", strip=True).split()
    ) if status_node else ""
    matched_statuses = [
        value for value in SELECTED_SOURCE_STATUSES if value in status_text
    ]
    if len(matched_statuses) != 1:
        raise MetsCatalogueScanError(
            f"M-ETS card has an unexpected selected-scope status: {external_id}"
        )

    price_nodes = card.select('[itemprop="price"][content]')
    if len(price_nodes) != 1:
        raise MetsCatalogueScanError("M-ETS card price evidence is ambiguous")
    price = _normal_price(str(price_nodes[0].get("content") or ""))
    return external_id, price, matched_statuses[0], title


def _parse_page(html: str, page_number: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    _verify_selected_filters(soup)
    if not _selected_page_is_visible(soup, page_number):
        raise MetsCatalogueScanError(
            f"M-ETS requested page {page_number} is not visibly selected; "
            f"paginator={_paginator_marker_summary(soup)}"
        )
    total = _reported_total(soup)
    cards = soup.select(".card-so")
    if not cards:
        raise MetsCatalogueScanError("M-ETS selected catalogue page has no cards")
    observations = [_card_observation(card) for card in cards]
    ids = [item[0] for item in observations]
    if len(set(ids)) != len(ids):
        raise MetsCatalogueScanError("M-ETS duplicate lot identity within page")

    discovered = {
        value.rsplit("/", 1)[-1] for value in discover_lot_urls(html, limit=50)
    }
    if discovered != set(ids):
        raise MetsCatalogueScanError(
            "M-ETS card identities differ from canonical catalogue link discovery"
        )
    next_page = _next_page(soup, page_number)
    return {
        "total": total,
        "observations": observations,
        "next_page": next_page,
    }


def _request_page(
    session: requests.Session,
    page_number: int,
    *,
    sleep: Callable[[float], None],
    attempts: int = 3,
) -> requests.Response:
    url = _page_url(page_number)
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                headers={
                    "User-Agent": "TORGI-METS-full-readonly-audit/1",
                    "Accept": "text/html",
                },
            )
        except requests.RequestException:
            if attempt >= attempts:
                raise
            sleep(float(2 ** (attempt - 1)))
            continue
        if response.status_code != 200:
            raise MetsCatalogueScanError(
                f"M-ETS page {page_number} returned HTTP {response.status_code}"
            )
        if len(response.content) > MAX_HTML_BYTES:
            raise MetsCatalogueScanError("M-ETS catalogue response exceeds size limit")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise MetsCatalogueScanError("M-ETS catalogue response is not HTML")
        if getattr(response, "url", url) != url:
            raise MetsCatalogueScanError("M-ETS response URL differs from requested URL")
        return response
    raise AssertionError("unreachable M-ETS retry loop")


def scan_complete_mets_catalogue(
    session: requests.Session,
    *,
    max_pages: int = MAX_SCAN_PAGES,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Walk one selected-catalogue pass or fail without claiming source coverage."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_SCAN_PAGES:
        raise MetsCatalogueScanError("Unsafe M-ETS max_pages")
    if not 0 <= delay_seconds <= 10:
        raise MetsCatalogueScanError("M-ETS delay must be 0..10 seconds")

    seen: set[str] = set()
    records: list[str] = []
    pages: list[dict[str, Any]] = []
    first_page_size: int | None = None
    reported_total: int | None = None
    last_page_proven = False
    started = time.monotonic()

    for page_number in range(1, max_pages + 1):
        if page_number > 1:
            sleep(delay_seconds)
        print(f"M-ETS requesting page={page_number}...", flush=True)
        response = _request_page(session, page_number, sleep=sleep)
        parsed = _parse_page(response.text, page_number)
        observations = parsed["observations"]
        ids = [item[0] for item in observations]
        if first_page_size is None:
            first_page_size = len(ids)
            reported_total = parsed["total"]
            if first_page_size > reported_total:
                raise MetsCatalogueScanError("M-ETS first page exceeds reported total")
        elif parsed["total"] != reported_total:
            raise MetsCatalogueScanError("M-ETS reported total changed within one pass")
        overlap = seen.intersection(ids)
        if overlap:
            raise MetsCatalogueScanError(
                "M-ETS cross-page duplicate lot identity"
            )
        seen.update(ids)
        records.extend(
            f"{external_id}\t{price}\t{status}\t{title}"
            for external_id, price, status, title in observations
        )
        status_counts = Counter(item[2] for item in observations)
        next_page = parsed["next_page"]
        if next_page is not None and len(ids) != first_page_size:
            raise MetsCatalogueScanError(
                "M-ETS non-terminal page size differs from the first page"
            )
        pages.append({
            "page": page_number,
            "items": len(ids),
            "status_counts": dict(sorted(status_counts.items())),
            "ids_sha256": hashlib.sha256(
                "\n".join(sorted(ids)).encode("ascii")
            ).hexdigest(),
            "records_sha256": hashlib.sha256(
                "\n".join(sorted(
                    f"{external_id}\t{price}\t{status}\t{title}"
                    for external_id, price, status, title in observations
                )).encode("utf-8")
            ).hexdigest(),
            "next_page": next_page,
        })
        print(
            f"M-ETS readonly page={page_number} items={len(ids)} "
            f"unique={len(seen)} total={reported_total} "
            f"next={next_page or 'none'} elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )
        if next_page is None:
            expected_last = math.ceil(reported_total / first_page_size)
            if page_number != expected_last:
                raise MetsCatalogueScanError(
                    "M-ETS terminal page contradicts reported total and page size"
                )
            if len(ids) >= first_page_size:
                raise MetsCatalogueScanError(
                    "M-ETS terminal page is not the required incomplete final page"
                )
            if len(seen) != reported_total:
                raise MetsCatalogueScanError(
                    "M-ETS unique identity count contradicts reported total"
                )
            last_page_proven = True
            break
        if next_page != page_number + 1:
            raise MetsCatalogueScanError("M-ETS pagination is not sequential")
    else:
        raise MetsCatalogueScanError("M-ETS page budget exhausted before final page")

    return {
        "source": "m-ets.ru",
        "mode": "full_readonly_selected_catalogue_walk",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "catalogue_url": APARTMENTS_URL,
        "selected_category_ids": list(SELECTED_CATEGORY_IDS),
        "selected_status_ids": list(SELECTED_STATUS_IDS),
        "selected_source_statuses": list(SELECTED_SOURCE_STATUSES),
        "filters_proven_on_every_page": True,
        "reported_total": reported_total,
        "pages_scanned": len(pages),
        "last_page": pages[-1]["page"],
        "last_page_proven": last_page_proven,
        "unique_lot_ids": len(seen),
        "lot_ids_sha256": hashlib.sha256(
            "\n".join(sorted(seen)).encode("ascii")
        ).hexdigest(),
        "catalogue_records_sha256": hashlib.sha256(
            "\n".join(sorted(records)).encode("utf-8")
        ).hexdigest(),
        "pages": pages,
        "catalogue_prices_verified": True,
        "catalogue_statuses_verified": True,
        "complete_selected_filter_inventory": last_page_proven,
        "complete_source_inventory": False,
        "category_scope_complete": False,
        "omitted_property_category_ids": list(OMITTED_PROPERTY_CATEGORY_IDS),
        "detail_validation_proven": False,
        "stable_point_in_time_snapshot": False,
        "production_database_writes": 0,
        "archival_allowed": False,
        "reconcile_missing": False,
        "geocoding": False,
        "map_publication": False,
    }


def run_two_independent_passes(
    session_factory: Callable[[], requests.Session] = requests.Session,
    *,
    max_pages: int = MAX_SCAN_PAGES,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    passes: list[dict[str, Any]] = []
    for pass_number in range(1, 3):
        print(f"M-ETS independent pass {pass_number}/2", flush=True)
        session = session_factory()
        try:
            passes.append(scan_complete_mets_catalogue(
                session,
                max_pages=max_pages,
                delay_seconds=delay_seconds,
            ))
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    stable = (
        passes[0]["reported_total"] == passes[1]["reported_total"]
        and passes[0]["last_page"] == passes[1]["last_page"]
        and passes[0]["unique_lot_ids"] == passes[1]["unique_lot_ids"]
        and passes[0]["lot_ids_sha256"] == passes[1]["lot_ids_sha256"]
        and passes[0]["catalogue_records_sha256"]
        == passes[1]["catalogue_records_sha256"]
    )
    return {
        "source": "m-ets.ru",
        "independent_passes": passes,
        "passes_match": stable,
        "last_page_proven_twice": stable and all(
            item["last_page_proven"] for item in passes
        ),
        "filters_proven_twice": stable and all(
            item["filters_proven_on_every_page"] for item in passes
        ),
        "catalogue_prices_and_statuses_proven_twice": stable and all(
            item["catalogue_prices_verified"] and item["catalogue_statuses_verified"]
            for item in passes
        ),
        "complete_selected_filter_inventory_twice": stable and all(
            item["complete_selected_filter_inventory"] for item in passes
        ),
        "complete_source_inventory": False,
        "category_scope_complete": False,
        "detail_validation_proven": False,
        "production_database_writes": 0,
        "archival_allowed": False,
        "reconcile_missing": False,
        "geocoding": False,
        "map_publication": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=MAX_SCAN_PAGES)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    report = run_two_independent_passes(
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report["passes_match"]:
        raise SystemExit("M-ETS independent selected-catalogue passes differ")
    if not report["last_page_proven_twice"] or not report["filters_proven_twice"]:
        raise SystemExit("M-ETS two-pass selected-catalogue proof is incomplete")


if __name__ == "__main__":
    main()
