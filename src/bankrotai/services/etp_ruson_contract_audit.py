"""Read-only RUSSIA OnLine source-contract audit.

The probe proves terminal pagination with bounded direct page requests, compares
two read-only passes over representative pages, then checks a diverse detail sample. It never imports database
modules, writes PostgreSQL, reconciles missing lots, archives records, or
publishes map data.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from typing import Any, Callable

from bankrotai.connectors.etp_sources.ruson import RusonPageRangeError, RusonReadonlyConnector


SOURCE = "rus-on.ru"
TARGET_VARIANT_IDS = (
    "rus-on.ru:115172",  # current ordinary auction / real estate
    "rus-on.ru:115160",  # ordinary auction / land + building
    "rus-on.ru:115156",  # current ordinary auction / land
    "rus-on.ru:115117",  # active public offer
    "rus-on.ru:114013",  # public-offer period prices with kopecks
    "rus-on.ru:113831",  # ended public offer
    "rus-on.ru:27424",   # historical no-RUB production report
    "rus-on.ru:21875",   # historical decimal production report
)


def _digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _even_sample(values: list[str], count: int) -> list[str]:
    if count <= 0 or not values:
        return []
    if count >= len(values):
        return list(values)
    if count == 1:
        return [values[len(values) // 2]]
    indexes = {
        round(index * (len(values) - 1) / (count - 1))
        for index in range(count)
    }
    return [values[index] for index in sorted(indexes)]


async def _probe_catalogue_page(
    connector: Any,
    page: int,
    *,
    delay_seconds: float,
) -> dict[str, Any]:
    try:
        result = await connector.search({}, cursor=str(page))
    except RusonPageRangeError as exc:
        return {
            "page": page,
            "ids": [],
            "count": 0,
            "next_cursor": None,
            "valid": False,
            "digest": _digest([]),
            "reject_reason": type(exc).__name__,
        }
    ids = [str(item.external_id) for item in result.items]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"rus-on page {page} repeats a lot ID inside the page")
    expected_next = str(page + 1)
    valid = bool(ids) and result.next_cursor in {None, expected_next}
    probe = {
        "page": page,
        "ids": ids,
        "count": len(ids),
        "next_cursor": result.next_cursor,
        "valid": valid,
        "digest": _digest(ids),
    }
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    return probe


async def _discover_terminal_page(
    connector: Any,
    *,
    max_pages: int,
    delay_seconds: float,
) -> tuple[int, dict[int, dict[str, Any]]]:
    """Find the terminal page with direct GET probes, never assuming a catalogue total."""
    probes: dict[int, dict[str, Any]] = {}

    async def probe(page: int) -> dict[str, Any]:
        if page not in probes:
            probes[page] = await _probe_catalogue_page(
                connector, page, delay_seconds=delay_seconds
            )
        return probes[page]

    first = await probe(1)
    if not first["valid"]:
        raise RuntimeError("rus-on page 1 does not satisfy the verified pagination contract")
    if first["next_cursor"] is None:
        return 1, probes

    lower = 1
    upper: int | None = None
    page = 2
    while page <= max_pages:
        item = await probe(page)
        if not item["valid"]:
            upper = page
            break
        lower = page
        if item["next_cursor"] is None:
            return page, probes
        page *= 2

    if upper is None:
        raise RuntimeError(
            "rus-on terminal page was not bounded within the configured page-number safety cap"
        )

    while upper - lower > 1:
        middle = (lower + upper) // 2
        item = await probe(middle)
        if item["valid"]:
            if item["next_cursor"] is None:
                return middle, probes
            lower = middle
        else:
            upper = middle

    last = await probe(lower)
    if last["next_cursor"] is None:
        return lower, probes
    candidate = await probe(lower + 1)
    if candidate["valid"] and candidate["next_cursor"] is None:
        return lower + 1, probes
    raise RuntimeError("rus-on terminal page could not be proven from provider next-page semantics")


def _sample_pages(terminal_page: int) -> list[int]:
    points = {
        1,
        min(2, terminal_page),
        max(1, terminal_page // 4),
        max(1, terminal_page // 2),
        max(1, (terminal_page * 3) // 4),
        terminal_page,
    }
    return sorted(points)


async def _sample_catalogue_pass(
    connector: Any,
    pages: list[int],
    *,
    delay_seconds: float,
) -> dict[str, Any]:
    ordered: list[str] = []
    seen: set[str] = set()
    page_counts: dict[int, int] = {}
    page_digests: dict[int, str] = {}
    for page in pages:
        item = await _probe_catalogue_page(connector, page, delay_seconds=delay_seconds)
        if not item["valid"]:
            raise RuntimeError(f"rus-on sampled page {page} no longer satisfies pagination contract")
        overlap = seen.intersection(item["ids"])
        if overlap:
            raise RuntimeError("rus-on sampled catalogue pages repeat lot IDs across pages")
        seen.update(item["ids"])
        ordered.extend(item["ids"])
        page_counts[page] = item["count"]
        page_digests[page] = item["digest"]
    return {
        "ids": ordered,
        "pages": pages,
        "page_counts": page_counts,
        "page_digests": page_digests,
        "unique_ids": len(seen),
        "ordered_id_digest": _digest(ordered),
    }


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if 0 < number < 1e15 else None


def _has_kopecks(value: object) -> bool:
    number = _safe_float(value)
    if number is None:
        return False
    return abs(number * 100 - round(number * 100)) < 1e-7 and abs(number - round(number)) > 1e-7


async def audit_ruson_contract(
    *,
    target_details: int = 35,
    max_attempts: int = 50,
    max_pages: int = 500,
    delay_seconds: float = 0.35,
    connector_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    if type(target_details) is not int or not 1 <= target_details <= 60:
        raise ValueError("target_details must be in 1..60")
    if type(max_attempts) is not int or not target_details <= max_attempts <= 80:
        raise ValueError("max_attempts must be between target_details and 80")
    if type(max_pages) is not int or not 2 <= max_pages <= 100_000:
        raise ValueError("max_pages must be in 2..100000")
    if not 0 <= delay_seconds <= 5:
        raise ValueError("delay_seconds must be in 0..5")

    factory = connector_factory or RusonReadonlyConnector
    terminal_page, terminal_probes = await _discover_terminal_page(
        factory(), max_pages=max_pages, delay_seconds=delay_seconds
    )
    pages = _sample_pages(terminal_page)
    first = await _sample_catalogue_pass(factory(), pages, delay_seconds=delay_seconds)
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    second = await _sample_catalogue_pass(factory(), pages, delay_seconds=delay_seconds)

    if first["ids"] != second["ids"]:
        raise RuntimeError("rus-on two-pass sampled catalogue identity sequence changed during audit")
    if first["ordered_id_digest"] != second["ordered_id_digest"]:
        raise RuntimeError("rus-on two-pass sampled catalogue digest mismatch")

    catalogue_ids = list(first["ids"])
    candidate_ids: list[str] = []
    for external_id in TARGET_VARIANT_IDS:
        if external_id not in candidate_ids:
            candidate_ids.append(external_id)
    for external_id in _even_sample(catalogue_ids, max_attempts):
        if external_id not in candidate_ids:
            candidate_ids.append(external_id)

    detail_connector = factory()
    counters: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    for external_id in candidate_ids:
        if counters["detail_attempts"] >= max_attempts or counters["details_verified"] >= target_details:
            break
        counters["detail_attempts"] += 1
        try:
            detail = await detail_connector.fetch_lot(external_id)
        except Exception as exc:
            failures[type(exc).__name__] += 1
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            continue
        if detail.external_id != external_id or detail.source_system != SOURCE:
            raise RuntimeError("rus-on detail identity does not match requested lot_nid")
        counters["details_verified"] += 1
        if detail.start_price is not None:
            counters["initial_price_ok"] += 1
        if detail.auction_status != "unknown":
            counters["status_ok"] += 1
        if detail.application_deadline is not None:
            counters["deadline_ok"] += 1
        if detail.application_start_at is not None:
            counters["application_start_ok"] += 1
        if detail.auction_status in {"active", "scheduled"}:
            counters["active_or_scheduled_details"] += 1
        if detail.auction_status == "closed":
            counters["ended_details"] += 1
        if detail.auction_type == "auction":
            counters["ordinary_auction_details"] += 1
        if detail.auction_type == "public_offer":
            counters["public_offer_details"] += 1
            if detail.public_offer_schedule:
                counters["public_offer_schedule_ok"] += 1
            if detail.current_price is not None:
                counters["public_offer_current_price_ok"] += 1
        if detail.raw_data.get("ruson_initial_price_had_currency") is True:
            counters["initial_price_with_currency"] += 1
        if detail.raw_data.get("ruson_initial_price_had_currency") is False:
            counters["initial_price_without_currency"] += 1
        if detail.raw_data.get("ruson_public_offer_currency") == "RUB":
            counters["currency_labelled_price"] += 1
        if _has_kopecks(detail.start_price) or _has_kopecks(detail.current_price):
            counters["price_with_kopecks"] += 1
        schedule = detail.public_offer_schedule or []
        if any(_has_kopecks(period.get("price")) for period in schedule if isinstance(period, dict)):
            counters["schedule_price_with_kopecks"] += 1
        samples.append({
            "external_id": external_id,
            "auction_status": detail.auction_status,
            "auction_type": detail.auction_type,
            "category": detail.category,
            "start_price": _safe_float(detail.start_price),
            "current_price": _safe_float(detail.current_price),
            "application_start": bool(detail.application_start_at),
            "application_deadline": bool(detail.application_deadline),
            "auction_at": bool(detail.auction_at),
            "schedule_periods": len(schedule),
            "initial_price_format": detail.raw_data.get("ruson_initial_price_format"),
            "initial_price_had_currency": detail.raw_data.get("ruson_initial_price_had_currency"),
        })
        if delay_seconds:
            await asyncio.sleep(delay_seconds)

    verified = counters["details_verified"]
    if verified < target_details:
        raise RuntimeError(
            f"rus-on detail sample incomplete: verified={verified}, target={target_details}"
        )

    def rate(key: str) -> float:
        return round(counters[key] / verified, 4) if verified else 0.0

    return {
        "source": SOURCE,
        "mode": "terminal_discovery_plus_two_pass_page_sample_plus_detail_sample",
        "full_catalogue_two_pass": False,
        "catalogue_pages_pass_1": len(first["pages"]),
        "catalogue_pages_pass_2": len(second["pages"]),
        "catalogue_pages_sampled": first["pages"],
        "catalogue_page_counts": first["page_counts"],
        "terminal_discovery_probe_pages": sorted(terminal_probes),
        "terminal_page": terminal_page,
        "terminal_page_proven": True,
        "unique_catalogue_ids": first["unique_ids"],
        "ordered_id_digest": first["ordered_id_digest"],
        "two_pass_ids_equal": first["ids"] == second["ids"],
        "detail_attempts": counters["detail_attempts"],
        "details_verified": verified,
        "initial_price_success_rate": rate("initial_price_ok"),
        "status_success_rate": rate("status_ok"),
        "application_deadline_success_rate": rate("deadline_ok"),
        "application_start_success_rate": rate("application_start_ok"),
        "active_or_scheduled_details": counters["active_or_scheduled_details"],
        "ended_details": counters["ended_details"],
        "ordinary_auction_details": counters["ordinary_auction_details"],
        "public_offer_details": counters["public_offer_details"],
        "public_offer_schedule_ok": counters["public_offer_schedule_ok"],
        "public_offer_current_price_ok": counters["public_offer_current_price_ok"],
        "initial_price_with_currency": counters["initial_price_with_currency"],
        "initial_price_without_currency": counters["initial_price_without_currency"],
        "currency_labelled_price": counters["currency_labelled_price"],
        "price_with_kopecks": counters["price_with_kopecks"],
        "schedule_price_with_kopecks": counters["schedule_price_with_kopecks"],
        "failure_classes": dict(failures),
        "sample": samples,
        "read_only": True,
        "production_database_writes": 0,
        "production_enabled": False,
        "reconcile_missing": False,
        "archival_allowed": False,
        "map_publication": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=int, default=35)
    parser.add_argument("--max-attempts", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=10_000)
    parser.add_argument("--delay-seconds", type=float, default=0.35)
    args = parser.parse_args()
    report = asyncio.run(audit_ruson_contract(
        target_details=args.details,
        max_attempts=args.max_attempts,
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
    ))
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
