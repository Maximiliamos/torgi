"""Bounded, read-only per-lot evidence for NISTP and CDTRF.

This is a D1-detail QUALITY SAMPLE, not a repeat of the complete catalogue
inventory and never an import authorization. Reports contain IDs, field
presence, and safe failure codes, not raw provider HTML or debtor details.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES
from bankrotai.connectors.etp_sources.common import _parse_soup
from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILE_BY_ID, parse_nistp_lot_observations,
)
from bankrotai.services.etp_cdtrf_canary import (
    API, MAX_RESPONSE, REQUEST_TIMEOUT, fetch_verified_cdtrf_lot,
)
from bankrotai.services.etp_nistp_full_catalogue import _ACTIVE_STATE_KEYS

_ALLOWED_SOURCES = frozenset({"nistp", "cdtrf", "both"})
# Same exact selected partition as the last clean 64-page CDTRF operator walk.
_CDTRF_SELECTED_STATUSES = ("Declare", "RecieveReq", "DefinePart", "TradeGo")
_CDTRF_SELECTED_CATEGORIES = "21,22,23,31,32,33,34,41"


def spaced_pages(last_page: int, count: int) -> list[int]:
    """Evenly spaced, deterministic pages without replacement."""
    if type(last_page) is not int or not 1 <= last_page <= 10_000:
        raise ValueError("Untrusted last_page")
    if type(count) is not int or not 1 <= count <= last_page:
        raise ValueError("Insufficient pages for distinct samples")
    if count == 1:
        return [1]
    return [1 + index * (last_page - 1) // (count - 1) for index in range(count)]


def planned_pages(last_page: int, sample_size: int) -> list[int]:
    """Primary spread plus bounded reserve for non-property/closed pages."""
    if not 1 <= sample_size <= 50 or sample_size > last_page:
        raise ValueError("Sample size exceeds approved page range")
    primary = spaced_pages(last_page, sample_size)
    reserve = spaced_pages(last_page, min(last_page, sample_size * 2))
    return primary + [page for page in reserve if page not in primary]


def _money(value: object) -> Decimal | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            return None
        return number if number == number.quantize(Decimal("0.01")) else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _same_initial_price(a: object, b: object) -> bool:
    left, right = _money(a), _money(b)
    return left is not None and right is not None and abs(left - right) <= Decimal("0.01")


def _fresh_report(source: str, requested: int, last_page: int) -> dict[str, Any]:
    return {
        "source": source,
        "mode": "bounded_independent_readonly_lot_detail_sample",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_page_from_previous_full_walk": last_page,
        "selected_cdtrf_categories": _CDTRF_SELECTED_CATEGORIES if source == "cdtrf.ru" else None,
        "sample_requested": requested,
        "sample_attempted": 0,
        "sample_verified": 0,
        "skipped_pages": 0,
        "observations": [],
        "errors": [],
        "evidence_scope": "sample_only_not_full_source_acceptance",
        "full_catalogue_inventory_proven_by_this_run": False,
        "full_detail_coverage_proven": False,
        "source_accepted": False,
        "production_database_writes": 0,
        "reconcile_missing": False,
        "archival_allowed": False,
        "geocoding": False,
        "map_publication": False,
    }


def _record_error(report: dict[str, Any], page: int, code: str,
                  external_id: str | None = None) -> None:
    # Never record exception text: provider errors can contain URLs or PII.
    record: dict[str, Any] = {"page": page, "code": code[:96]}
    if external_id is not None:
        record["external_id"] = external_id
    report["errors"].append(record)


def _finish(report: dict[str, Any]) -> dict[str, Any]:
    report["error_counts"] = dict(sorted(Counter(
        item["code"] for item in report["errors"]
    ).items()))
    report["detail_sample_pass"] = (
        report["sample_attempted"] == report["sample_requested"]
        and report["sample_verified"] == report["sample_requested"]
        and not report["errors"]
    )
    return report


async def check_nistp_details(
    last_page: int, sample_size: int, *,
    delay_seconds: float = 1.0,
    connector_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    report = _fresh_report("nistp.ru", sample_size, last_page)
    factory = connector_factory or SOURCE_CONNECTOR_TYPES["nistp.ru"]
    pages_requested = 0
    for page in planned_pages(last_page, sample_size):
        if report["sample_attempted"] >= sample_size:
            break
        if pages_requested:
            await asyncio.to_thread(sleep, delay_seconds)
        pages_requested += 1
        connector = factory()
        try:
            result = await connector.search({}, cursor=str(page))
            response = connector._last_response
            if response is None or not response.content:
                raise ValueError("missing catalogue response")
            observed = parse_nistp_lot_observations(
                PROFILE_BY_ID["nistp.ru"],
                _parse_soup(response.content).decode(), limit=100,
            )
            active = [
                row for row in observed
                if row["is_property"]
                and row["source_status_text"].casefold().replace("ё", "е")
                in _ACTIVE_STATE_KEYS
            ]
            candidate = next((
                row for row in sorted(active, key=lambda item: item["provider_external_id"])
                if row["provider_external_id"] in {
                    item.external_id for item in result.items
                }
            ), None)
            if candidate is None:
                report["skipped_pages"] += 1
                continue
            identity = candidate["provider_external_id"]
            report["sample_attempted"] += 1
            detail = await connector.fetch_lot(identity)
            if (detail.external_id != identity
                    or detail.source_system != "nistp.ru"
                    or detail.detail_level != "detail"
                    or detail.raw_data.get("individual_lot_identity_verified") is not True):
                _record_error(report, page, "detail_identity_unproven", identity)
                continue
            if not _same_initial_price(candidate["listing_price_rub"], detail.start_price):
                _record_error(report, page, "listing_detail_initial_price_mismatch", identity)
                continue
            catalogue_status = _status(candidate["source_status_text"])
            if (catalogue_status == "unknown"
                    or detail.auction_status != catalogue_status):
                _record_error(report, page, "listing_detail_status_mismatch", identity)
                continue
            if detail.category in {"other", "vehicle"}:
                _record_error(report, page, "detail_property_classification_unverified", identity)
                continue
            report["sample_verified"] += 1
            report["observations"].append({
                "page": page, "external_id": identity,
                "verified_lot_identity": True,
                "initial_price_matches_catalogue": True,
                "normalized_status_matches_catalogue": True,
                "detail_category": detail.category,
                "cadastre_present": bool(detail.cadastral_number),
                "address_present": bool(detail.address),
                "deposit_present": detail.deposit_amount is not None,
            })
        except Exception as exc:
            # Stop on explicit provider rate limiting/access denial rather than
            # repeatedly probing against a refusal.
            _record_error(report, page, type(exc).__name__)
            if any(code in str(exc) for code in ("HTTP 429", "HTTP 403", "HTTP 401")):
                break
        print(f"NISTP detail QA: page={page} attempted={report['sample_attempted']}/{sample_size} verified={report['sample_verified']} issues={len(report['errors'])}", file=sys.stderr, flush=True)
    return _finish(report)


def _cdtrf_page(session: requests.Session, page: int) -> dict[str, Any]:
    params = [(name, "true") for name in _CDTRF_SELECTED_STATUSES]
    params.extend([
        ("Categories", _CDTRF_SELECTED_CATEGORIES),
        ("PageSize", "100"),
        ("PageNum", str(page)),
    ])
    url = f"{API}?{urlencode(params)}"
    response = session.get(
        url, allow_redirects=False, timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": "TORGI-CDTRF-readonly-detail-QA/1"},
    )
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
        raise ValueError("CDTRF catalogue HTTP/schema refusal")
    if "json" not in response.headers.get("Content-Type", "").casefold():
        raise ValueError("CDTRF catalogue response is not JSON")
    return parse_cdtrf_api_page(response.json(), page_num=page, page_size=100)


def check_cdtrf_details(
    last_page: int, sample_size: int, *,
    delay_seconds: float = 1.0,
    session: requests.Session | None = None,
    fetch_detail: Callable[..., Any] = fetch_verified_cdtrf_lot,
    fetch_page: Callable[..., dict[str, Any]] = _cdtrf_page,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    report = _fresh_report("cdtrf.ru", sample_size, last_page)
    own = session or requests.Session()
    pages_requested = 0
    for page in planned_pages(last_page, sample_size):
        if report["sample_attempted"] >= sample_size:
            break
        if pages_requested:
            sleep(delay_seconds)
        pages_requested += 1
        try:
            data = fetch_page(own, page)
            candidates = data["property_candidates"]
            if not candidates:
                report["skipped_pages"] += 1
                continue
            candidate = sorted(candidates, key=lambda row: int(row["trade_lot_id"]))[0]
            trade_id, lot_id = candidate["trade_id"], candidate["trade_lot_id"]
            identity = candidate["external_id"]
            report["sample_attempted"] += 1
            detail = fetch_detail(trade_id, lot_id, session=own)
            raw = detail.raw_data or {}
            if (detail.external_id != identity or detail.source_system != "cdtrf.ru"
                    or raw.get("trade_id") != trade_id
                    or str(raw.get("trade_lot_id")) != str(lot_id)
                    or detail.detail_level != "public_detail"):
                _record_error(report, page, "detail_identity_unproven", identity)
                continue
            if _money(detail.start_price) is None:
                _record_error(report, page, "detail_initial_price_missing", identity)
                continue
            if detail.auction_status not in {"active", "scheduled"}:
                _record_error(report, page, "detail_status_out_of_selected_scope", identity)
                continue
            if detail.category in {"other", "vehicle"}:
                _record_error(report, page, "detail_property_classification_unverified", identity)
                continue
            report["sample_verified"] += 1
            report["observations"].append({
                "page": page, "external_id": identity,
                "verified_trade_and_lot_identity": True,
                "initial_price_verified_in_detail": True,
                "catalogue_actual_price_not_assumed_initial": True,
                "detail_status": detail.auction_status,
                "detail_category": detail.category,
                "cadastre_present": bool(detail.cadastral_number),
                "address_present": bool(detail.address),
                "property_right_kind": raw.get("property_right_kind", "unverified"),
            })
        except Exception as exc:
            _record_error(report, page, type(exc).__name__)
            if any(code in str(exc) for code in ("HTTP 429", "HTTP 403", "HTTP 401")):
                break
        print(f"CDTRF detail QA: page={page} attempted={report['sample_attempted']}/{sample_size} verified={report['sample_verified']} issues={len(report['errors'])}", file=sys.stderr, flush=True)
    return _finish(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(_ALLOWED_SOURCES), default="both")
    parser.add_argument("--nistp-last-page", type=int, default=142)
    parser.add_argument("--cdtrf-last-page", type=int, default=64)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--evidence-root", type=Path,
                        default=Path("D:/BankrotAI/etp-evidence/two-source-detail-qa"))
    args = parser.parse_args()
    if not 0.5 <= args.delay_seconds <= 10:
        parser.error("Delay must be 0.5..10 seconds")
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reports = []
    if args.source in {"nistp", "both"}:
        print("NISTP read-only per-lot detail sample started", file=sys.stderr, flush=True)
        reports.append(asyncio.run(check_nistp_details(
            args.nistp_last_page, args.sample_size, delay_seconds=args.delay_seconds,
        )))
    if args.source in {"cdtrf", "both"}:
        print("CDTRF read-only per-lot detail sample started", file=sys.stderr, flush=True)
        reports.append(check_cdtrf_details(
            args.cdtrf_last_page, args.sample_size, delay_seconds=args.delay_seconds,
        ))
    summary = []
    for report in reports:
        path = args.evidence_root / f"{report['source'].replace('.', '-')}-{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Read-only sample evidence: {path}", file=sys.stderr, flush=True)
        summary.append({
            "source": report["source"],
            "attempted": report["sample_attempted"],
            "verified": report["sample_verified"],
            "sample_pass": report["detail_sample_pass"],
            "errors": report["error_counts"],
            "evidence_path": str(path),
            "source_accepted": False,
        })
    print(json.dumps({"reports": summary, "production_database_writes": 0,
                      "archival_allowed": False}, ensure_ascii=False))
    if any(not report["detail_sample_pass"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
