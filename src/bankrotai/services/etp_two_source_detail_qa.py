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
import re
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES, SourceAccessError
from bankrotai.connectors.etp_sources.nistp_detail_sections import NistpCardError
from bankrotai.connectors.registry.cdtrf_public import CdtrfEvidenceError
from bankrotai.connectors.etp_sources.common import _parse_soup
from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILE_BY_ID, PROPERTY_WORDS, parse_nistp_lot_observations,
)
from bankrotai.services.etp_cdtrf_canary import (
    API, MAX_RESPONSE, REQUEST_TIMEOUT, fetch_verified_cdtrf_lot,
)
from bankrotai.services.etp_nistp_full_catalogue import _ACTIVE_STATE_KEYS

_ALLOWED_SOURCES = frozenset({"nistp", "cdtrf", "both"})
# Same exact selected partition as the last clean 64-page CDTRF operator walk.
_CDTRF_SELECTED_STATUSES = ("Declare", "RecieveReq", "DefinePart", "TradeGo")
_CDTRF_SELECTED_CATEGORIES = "21,22,23,31,32,33,34,41"
_NON_PROPERTY_RECEIVABLE = re.compile(r"дебиторск|право\s+требован", re.IGNORECASE)

# Only developer-controlled constant exception strings are reportable. Never
# emit arbitrary exception text or fetched provider HTML / personal details.
_NISTP_DETAIL_REASONS = frozenset({
    "invalid_trade_nid", "missing_trade_code", "missing_numbered_lot_sections",
    "invalid_individual_lot_table_id", "duplicate_lot_section_number",
    "lot_heading_does_not_match_table", "lot_number_anchor_missing",
    "multiple_native_lot_identifiers", "duplicate_native_lot_identifier",
    "duplicate_individual_lot_field", "incomplete_numbered_lot_section",
    "lot_number_field_mismatch", "invalid_individual_lot_money",
    "invalid_individual_lot_status", "requested_lot_missing_in_trade_card",
    "invalid_lot_number",
})
_CDTRF_DETAIL_REASONS = {
    "Invalid CDTRF Nuxt reference": "invalid_nuxt_reference",
    "No CDTRF SSR data": "missing_nuxt_ssr",
    "Invalid CDTRF SSR structure": "invalid_nuxt_ssr_structure",
    "Lot object missing from SSR": "lot_missing_from_ssr",
    "Trade ID or title disagreement between URL, HTML and Nuxt": "trade_identity_or_title_mismatch",
    "No stable CDTRF lot identity": "missing_stable_lot_identity",
    "Price disagreement between HTML and SSR": "html_ssr_initial_price_mismatch",
    "Invalid public-offer schedule": "invalid_public_offer_schedule",
    "Malformed price period": "malformed_price_period",
    "Incomplete price period": "incomplete_price_period",
    "Invalid CDTRF lot property description": "invalid_property_description",
    "Invalid CDTRF source detail URL": "invalid_detail_url",
}


def safe_detail_reason(exc: Exception) -> str | None:
    """Return a fixed PII-safe reason for provider-controlled parser failures."""
    if isinstance(exc, NistpCardError):
        reason = str(exc)
        return reason if reason in _NISTP_DETAIL_REASONS else None
    if isinstance(exc, CdtrfEvidenceError):
        return _CDTRF_DETAIL_REASONS.get(str(exc))
    return None


def checked_recheck_pages(last_page: int, pages: list[int]) -> list[int]:
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 50
            or any(type(p) is not int or not 1 <= p <= last_page for p in pages)
            or len(set(pages)) != len(pages)):
        raise ValueError("Invalid or duplicated recheck pages")
    return pages


def failed_pages_from_report(path: Path, *, source: str, last_page: int) -> list[int]:
    """Reuse only failure page numbers, not mutable source identities."""
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Prior QA report missing or oversized")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(data, dict) or data.get("source") != source
            or data.get("mode") != "bounded_independent_readonly_lot_detail_sample"
            or data.get("last_page_from_previous_full_walk") != last_page
            or data.get("source_accepted") is not False):
        raise ValueError("Previous QA report has inconsistent source or scope")
    errors = data.get("errors")
    if not isinstance(errors, list):
        raise ValueError("Previous QA report has no error list")
    pages: list[int] = []
    for entry in errors:
        if not isinstance(entry, dict) or type(entry.get("page")) is not int:
            raise ValueError("Malformed previous QA error")
        if entry["page"] not in pages:
            pages.append(entry["page"])
    return checked_recheck_pages(last_page, pages)




def pinned_failed_targets(path: Path, *, source: str, last_page: int) -> dict[int, str]:
    """Pin exact IDs from the newer source-safe QA reports, never page substitutes.

    The old page-only QA reports cannot prove that a subsequent 'first candidate'
    is the same rejected lot; this mode explicitly refuses that ambiguity.
    """
    pages = failed_pages_from_report(path, source=source, last_page=last_page)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    ids = re.compile(
        r"nistp\.ru:[1-9]\d{0,11}:[1-9]\d{0,4}\Z"
        if source == "nistp.ru" else r"cdtrf:[1-9]\d{2,11}\Z"
    )
    expected: dict[int, str] = {}
    for entry in data["errors"]:
        page = entry["page"]
        identity = entry.get("external_id")
        if (not isinstance(identity, str) or ids.fullmatch(identity) is None
                or entry.get("stage") != "detail" or page in expected
                or identity in expected.values()):
            raise ValueError("Prior failure report lacks trustworthy exact lot IDs")
        expected[page] = identity
    if list(expected) != pages:
        raise ValueError("Pin order or scope inconsistent")
    return expected


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
                  external_id: str | None = None, *, stage: str | None = None,
                  reason: str | None = None) -> None:
    # Never record exception text: provider errors can contain URLs or PII.
    record: dict[str, Any] = {"page": page, "code": code[:96]}
    if external_id is not None:
        record["external_id"] = external_id
    if stage in {"catalogue", "detail", "comparison"}:
        record["stage"] = stage
    if reason is not None:
        record["reason"] = reason
    report["errors"].append(record)


def _finish(report: dict[str, Any]) -> dict[str, Any]:
    report["error_counts"] = dict(sorted(Counter(
        item["code"] for item in report["errors"]
    ).items()))
    report["reason_counts"] = dict(sorted(Counter(
        item.get("reason") or "unclassified_" + item["code"]
        for item in report["errors"]
    ).items()))
    report["detail_sample_pass"] = (
        report["sample_attempted"] == report["sample_requested"]
        and report["sample_verified"] == report["sample_requested"]
        and not report["errors"]
    )
    # A validated Nuxt title is the source-of-truth for the individual lot,
    # while a different HTML H1 remains a separate operational review flag.
    report["source_title_disagreement_review_count"] = sum(
        item.get("source_title_disagreement_review_required") is True
        for item in report["observations"]
    )
    return report


def _emit_page_progress(
    source: str, report: dict[str, Any], page: int, requested: int,
    *, started: float, errors_before: int, verified_before: int,
) -> None:
    """Always report one line per page, including early continue and refusal."""
    if len(report["errors"]) > errors_before:
        outcome = report["errors"][-1]["code"]
    elif report["sample_verified"] > verified_before:
        outcome = "verified"
    else:
        outcome = "no_eligible_candidate"
    elapsed = max(0.0, time.monotonic() - started)
    print(
        f"{source} detail QA: page={page} "
        f"attempted={report['sample_attempted']}/{requested} "
        f"verified={report['sample_verified']} issues={len(report['errors'])} "
        f"outcome={outcome} elapsed_s={elapsed:.1f}",
        file=sys.stderr, flush=True,
    )


async def check_nistp_details(
    last_page: int, sample_size: int, *,
    delay_seconds: float = 1.0,
    connector_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], Any] = time.sleep,
    recheck_pages: list[int] | None = None,
    pinned_targets: dict[int, str] | None = None,
) -> dict[str, Any]:
    pages = (planned_pages(last_page, sample_size) if recheck_pages is None
             else checked_recheck_pages(last_page, recheck_pages))
    requested = sample_size if recheck_pages is None else len(pages)
    if pinned_targets is not None and set(pinned_targets) != set(pages):
        raise ValueError("Pinned NISTP ID pages differ from approved recheck pages")
    report = _fresh_report("nistp.ru", requested, last_page)
    report["recheck_only_prior_failure_pages"] = recheck_pages is not None
    report["exact_original_ids_pinned"] = pinned_targets is not None
    factory = connector_factory or SOURCE_CONNECTOR_TYPES["nistp.ru"]
    pages_requested = 0
    seen_sample_ids: set[str] = set()
    for page in pages:
        if report["sample_attempted"] >= requested:
            break
        if pages_requested:
            await asyncio.to_thread(sleep, delay_seconds)
        pages_requested += 1
        page_started = time.monotonic()
        errors_before = len(report["errors"])
        verified_before = report["sample_verified"]
        connector = factory()
        identity: str | None = None
        stage = "catalogue"
        try:
            result = await connector.search({}, cursor=str(page))
            response = getattr(connector, "_last_response", None)
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
            expected_id = pinned_targets.get(page) if pinned_targets else None
            listed_ids = {item.external_id for item in result.items}
            candidate = next((
                row for row in sorted(active, key=lambda item: item["provider_external_id"])
                if row["provider_external_id"] in listed_ids
                and (expected_id is None or row["provider_external_id"] == expected_id)
            ), None)
            if candidate is None:
                if expected_id is not None:
                    report["sample_attempted"] += 1
                    _record_error(
                        report, page, "pinned_lot_not_in_active_catalogue_page",
                        expected_id, stage="catalogue",
                    )
                else:
                    report["skipped_pages"] += 1
                continue
            identity = candidate["provider_external_id"]
            if identity in seen_sample_ids:
                _record_error(report, page, "duplicate_sample_identity", identity)
                continue
            seen_sample_ids.add(identity)
            report["sample_attempted"] += 1
            stage = "detail"
            detail = await connector.fetch_lot(identity)
            stage = "comparison"
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
            detail_text = f"{detail.title} {detail.description}"
            if (not PROPERTY_WORDS.search(detail_text)
                    or _NON_PROPERTY_RECEIVABLE.search(detail_text)):
                _record_error(report, page, "detail_property_evidence_unverified", identity)
                continue
            report["sample_verified"] += 1
            report["observations"].append({
                "page": page, "external_id": identity,
                "verified_lot_identity": True,
                "initial_price_matches_catalogue": True,
                "normalized_status_matches_catalogue": True,
                "detail_category": detail.category,
                "normalized_category_review_required": detail.category == "other",
                "detail_property_text_confirmed": True,
                "cadastre_present": bool(detail.cadastral_number),
                "address_present": bool(detail.address),
                "deposit_present": detail.deposit_amount is not None,
            })
        except Exception as exc:
            # Stop on explicit provider rate limiting/access denial rather than
            # repeatedly probing against a refusal.
            _record_error(
                report, page, type(exc).__name__, identity,
                stage=stage, reason=safe_detail_reason(exc),
            )
            if any(code in str(exc) for code in ("HTTP 429", "HTTP 403", "HTTP 401")):
                break
        finally:
            _emit_page_progress(
                "NISTP", report, page, requested, started=page_started,
                errors_before=errors_before, verified_before=verified_before,
            )
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
    if response.status_code in {401, 403, 429}:
        raise SourceAccessError(
            f"cdtrf.ru: provider returned HTTP {response.status_code}"
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
    recheck_pages: list[int] | None = None,
    pinned_targets: dict[int, str] | None = None,
) -> dict[str, Any]:
    pages = (planned_pages(last_page, sample_size) if recheck_pages is None
             else checked_recheck_pages(last_page, recheck_pages))
    requested = sample_size if recheck_pages is None else len(pages)
    if pinned_targets is not None and set(pinned_targets) != set(pages):
        raise ValueError("Pinned CDTRF ID pages differ from approved recheck pages")
    report = _fresh_report("cdtrf.ru", requested, last_page)
    report["recheck_only_prior_failure_pages"] = recheck_pages is not None
    report["exact_original_ids_pinned"] = pinned_targets is not None
    own = session or requests.Session()
    pages_requested = 0
    seen_sample_ids: set[str] = set()
    for page in pages:
        if report["sample_attempted"] >= requested:
            break
        if pages_requested:
            sleep(delay_seconds)
        pages_requested += 1
        page_started = time.monotonic()
        errors_before = len(report["errors"])
        verified_before = report["sample_verified"]
        identity: str | None = None
        stage = "catalogue"
        try:
            data = fetch_page(own, page)
            candidates = data["property_candidates"]
            if not candidates:
                if pinned_targets is not None:
                    report["sample_attempted"] += 1
                    _record_error(
                        report, page, "pinned_lot_not_in_active_catalogue_page",
                        pinned_targets[page], stage="catalogue",
                    )
                else:
                    report["skipped_pages"] += 1
                continue
            expected_id = pinned_targets.get(page) if pinned_targets else None
            candidate = next((
                row for row in sorted(candidates, key=lambda item: int(item["trade_lot_id"]))
                if expected_id is None or row["external_id"] == expected_id
            ), None)
            if candidate is None:
                # Live selected catalogues move. Do NOT silently inspect an
                # unrelated first lot and claim the old failed identity passed.
                report["sample_attempted"] += 1
                _record_error(
                    report, page, "pinned_lot_not_in_active_catalogue_page",
                    expected_id, stage="catalogue",
                )
                continue
            trade_id, lot_id = candidate["trade_id"], candidate["trade_lot_id"]
            identity = candidate["external_id"]
            if identity in seen_sample_ids:
                _record_error(report, page, "duplicate_sample_identity", identity)
                continue
            seen_sample_ids.add(identity)
            report["sample_attempted"] += 1
            stage = "detail"
            detail = fetch_detail(trade_id, lot_id, session=own)
            stage = "comparison"
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
                "html_title_matches_nuxt_lot_name": raw.get(
                    "html_title_matches_nuxt_lot_name"
                ),
                "source_title_disagreement_review_required": raw.get(
                    "source_title_disagreement_review_required", False
                ),
                "detail_status": detail.auction_status,
                "detail_category": detail.category,
                "cadastre_present": bool(detail.cadastral_number),
                "address_present": bool(detail.address),
                "property_right_kind": raw.get("property_right_kind", "unverified"),
            })
        except Exception as exc:
            _record_error(
                report, page, type(exc).__name__, identity,
                stage=stage, reason=safe_detail_reason(exc),
            )
            if any(code in str(exc) for code in ("HTTP 429", "HTTP 403", "HTTP 401")):
                break
        finally:
            _emit_page_progress(
                "CDTRF", report, page, requested, started=page_started,
                errors_before=errors_before, verified_before=verified_before,
            )
    return _finish(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(_ALLOWED_SOURCES), default="both")
    parser.add_argument("--nistp-last-page", type=int, default=142)
    parser.add_argument("--cdtrf-last-page", type=int, default=64)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--recheck-nistp-report", type=Path)
    parser.add_argument("--recheck-cdtrf-report", type=Path)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--evidence-root", type=Path,
                        default=Path("D:/BankrotAI/etp-evidence/two-source-detail-qa"))
    args = parser.parse_args()
    if not 0.5 <= args.delay_seconds <= 10:
        parser.error("Delay must be 0.5..10 seconds")
    if bool(args.recheck_nistp_report) != bool(args.recheck_cdtrf_report):
        parser.error("Both previous QA reports must be supplied for a pair recheck")
    nistp_targets = (pinned_failed_targets(
        args.recheck_nistp_report, source="nistp.ru", last_page=args.nistp_last_page,
    ) if args.recheck_nistp_report else None)
    cdtrf_targets = (pinned_failed_targets(
        args.recheck_cdtrf_report, source="cdtrf.ru", last_page=args.cdtrf_last_page,
    ) if args.recheck_cdtrf_report else None)
    nistp_pages = list(nistp_targets) if nistp_targets else None
    cdtrf_pages = list(cdtrf_targets) if cdtrf_targets else None
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reports = []
    if args.source in {"nistp", "both"}:
        print("NISTP read-only per-lot detail sample started", file=sys.stderr, flush=True)
        reports.append(asyncio.run(check_nistp_details(
            args.nistp_last_page, args.sample_size, delay_seconds=args.delay_seconds,
            recheck_pages=nistp_pages, pinned_targets=nistp_targets,
        )))
    if args.source in {"cdtrf", "both"}:
        print("CDTRF read-only per-lot detail sample started", file=sys.stderr, flush=True)
        reports.append(check_cdtrf_details(
            args.cdtrf_last_page, args.sample_size, delay_seconds=args.delay_seconds,
            recheck_pages=cdtrf_pages, pinned_targets=cdtrf_targets,
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
            "exact_original_ids_pinned": report["exact_original_ids_pinned"],
            "source_title_disagreement_review_count":
                report["source_title_disagreement_review_count"],
            "errors": report["error_counts"],
            "reasons": report["reason_counts"],
            "failed_lots": [
                {"page": item["page"], "external_id": item.get("external_id"),
                 "stage": item.get("stage"), "reason": item.get("reason")}
                for item in report["errors"][:50]
            ],
            "evidence_path": str(path),
            "source_accepted": False,
        })
    print(json.dumps({"reports": summary, "production_database_writes": 0,
                      "archival_allowed": False}, ensure_ascii=False))
    if any(not report["detail_sample_pass"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
