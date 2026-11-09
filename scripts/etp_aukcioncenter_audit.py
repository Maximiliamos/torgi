from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

import requests

from bankrotai.connectors.etp_sources.aukcioncenter import (
    LISTING_URL,
    catalogue_params,
    declared_pages,
    detail_request_url,
    discover_lot_numbers,
    lot_index_url,
    observation_digest,
    parse_detail,
    parse_listing,
    parse_procedure_observations,
    status_filter_options,
    terminal_page_contract,
)


@dataclass(frozen=True)
class Profile:
    source_id: str = "aukcioncenter.ru"
    platform_name: str = "Аукцион-центр"
    platform_code: str = "auctioncenter"


PROFILE = Profile()


def _get(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> requests.Response:
    response = session.get(
        url,
        params=params,
        timeout=(8, 35),
        allow_redirects=False,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "TORGI-AukcionCenter-readonly-audit/2026-11-09",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP_{response.status_code}")
    if len(response.content) > 4_000_000:
        raise RuntimeError("response_too_large")
    return response


def _body(response: requests.Response) -> bytes:
    return response.content


def _scan_pass(
    session: requests.Session,
    *,
    pause: float,
    pass_no: int,
) -> tuple[dict[str, Any], list[Any], list[Any], bytes, bytes]:
    first = _get(session, LISTING_URL, params=catalogue_params(1))
    first_pages = declared_pages(_body(first))
    declared_last = max(first_pages or (1,))
    pages: list[dict[str, Any]] = []
    observations: list[Any] = []
    lots: list[Any] = []
    last_body = _body(first)

    for page in range(1, declared_last + 1):
        response = first if page == 1 else _get(
            session, LISTING_URL, params=catalogue_params(page)
        )
        body = _body(response)
        page_observations = parse_procedure_observations(body, active_filter=True)
        page_lots = parse_listing(PROFILE, body, page=page, active_filter=True)
        pages.append({
            "page": page,
            "procedure_rows": len(page_observations),
            "individual_lots": len(page_lots),
            "digest": observation_digest(page_observations),
            "declared_pages": list(declared_pages(body)),
        })
        observations.extend(page_observations)
        lots.extend(page_lots)
        last_body = body
        if page != declared_last:
            time.sleep(pause)

    ids = [lot.external_id for lot in lots]
    duplicate_ids = sorted(value for value, count in Counter(ids).items() if count > 1)
    pass_result = {
        "pass": pass_no,
        "pages_scanned": declared_last,
        "declared_last_page": declared_last,
        "procedure_rows": len(observations),
        "individual_lot_candidates": len(ids),
        "unique_individual_lot_candidates": len(set(ids)),
        "duplicate_individual_lot_ids": duplicate_ids[:50],
        "rows_without_provider_id": sum(item.provider_id is None for item in observations),
        "rows_without_explicit_lot_identity": sum(not item.lot_numbers for item in observations),
        "mixed_property_procedure_rows": sum(item.mixed_property_classes for item in observations),
        "listing_initial_price_verified": sum(lot.start_price is not None for lot in lots),
        "listing_explicit_status_verified": sum(lot.auction_status != "unknown" for lot in lots),
        "listing_property_candidates": sum(bool(lot.raw_data.get("eligible_property")) for lot in lots),
        "digest": observation_digest(observations),
        "identity_digest": hashlib.sha256(
            "\n".join(sorted(set(ids))).encode("utf-8")
        ).hexdigest(),
    }
    return pass_result, observations, lots, last_body, _body(first)


def _status_filter_evidence(
    session: requests.Session,
    html: bytes,
    *,
    pause: float,
) -> dict[str, Any]:
    options = status_filter_options(html)
    evidence: dict[str, Any] = {
        "provider_options": options,
        "probes": {},
    }
    targets = {
        "active": ("приём заявок", "прием заявок"),
        "finished": ("заверш", "окончен"),
        "cancelled": ("отмен"),
    }
    for kind, needles in targets.items():
        match = next(
            (
                (label, value)
                for label, value in options.items()
                if any(needle in label for needle in needles)
            ),
            None,
        )
        if match is None:
            evidence["probes"][kind] = {
                "verified": False,
                "reason": "provider_filter_option_not_found",
            }
            continue
        label, value = match
        params = catalogue_params(1)
        params["processStatus"] = value
        time.sleep(pause)
        response = _get(session, LISTING_URL, params=params)
        observations = parse_procedure_observations(
            _body(response), active_filter=False
        )
        markers = Counter(
            item.source_status for item in observations if item.source_status
        )
        evidence["probes"][kind] = {
            "verified": bool(observations),
            "provider_label": label,
            "provider_value": value,
            "rows": len(observations),
            "explicit_status_markers": dict(markers),
        }
    return evidence


def _discover_unresolved_lots(
    session: requests.Session,
    observations: list[Any],
    *,
    pause: float,
    max_pages_per_procedure: int = 10,
) -> tuple[dict[str, tuple[int, ...]], list[dict[str, Any]]]:
    discovered: dict[str, tuple[int, ...]] = {}
    evidence: list[dict[str, Any]] = []
    for item in observations:
        provider_id = item.provider_id
        if provider_id is None or item.lot_numbers or provider_id in discovered:
            continue
        numbers: set[int] = set()
        seen_payloads: set[str] = set()
        pages_checked = 0
        for page in range(1, max_pages_per_procedure + 1):
            if pages_checked:
                time.sleep(pause)
            response = _get(session, lot_index_url(provider_id, page=page))
            body = _body(response)
            digest = hashlib.sha256(body).hexdigest()
            if digest in seen_payloads:
                break
            seen_payloads.add(digest)
            pages_checked += 1
            page_numbers = set(discover_lot_numbers(body))
            if not page_numbers:
                break
            before = len(numbers)
            numbers.update(page_numbers)
            if len(numbers) == before:
                break
        discovered[provider_id] = tuple(sorted(numbers))
        evidence.append({
            "provider_id": provider_id,
            "trade_number": item.trade_number,
            "pages_checked": pages_checked,
            "discovered_lot_numbers": list(sorted(numbers)),
        })
        time.sleep(pause)
    return discovered, evidence


def _detail_candidates(
    lots: list[Any],
    observations: list[Any],
    discovered: dict[str, tuple[int, ...]],
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for lot in lots:
        candidates[lot.external_id] = {
            "external_id": lot.external_id,
            "trade_number": lot.procedure_number,
            "property_hint": bool(lot.raw_data.get("eligible_property")),
            "category_hint": lot.category,
            "listing_initial_price": lot.start_price,
            "listing_status": lot.auction_status,
            "listing_lifecycle": lot.raw_data.get("source_lifecycle"),
            "origin": "catalogue_lot",
        }
    for obs in observations:
        if not obs.provider_id:
            continue
        for lot_number in discovered.get(obs.provider_id, ()):
            external_id = f"aukcioncenter.ru:{obs.provider_id}:{lot_number}"
            candidates.setdefault(external_id, {
                "external_id": external_id,
                "trade_number": obs.trade_number,
                "property_hint": lot_number in obs.property_lot_numbers,
                "category_hint": None,
                "listing_initial_price": None,
                "listing_status": "unknown",
                "listing_lifecycle": "unknown",
                "origin": "inner_lot_index",
            })

    # Preserve catalogue order. Sampling below round-robins public offers and
    # ordinary auctions instead of sorting by provider id, which biased older
    # public-offer procedures in the first audit.
    return list(candidates.values())


def _select_detail_candidates(
    candidates: list[dict[str, Any]], details: int,
) -> list[dict[str, Any]]:
    property_candidates = [item for item in candidates if item.get("property_hint")]
    fallback = [item for item in candidates if not item.get("property_hint")]
    ordered = property_candidates + fallback
    public_offer = [
        item for item in ordered if "ОТПП" in str(item.get("trade_number") or "")
    ]
    ordinary = [
        item for item in ordered if "ОТПП" not in str(item.get("trade_number") or "")
    ]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in range(max(len(public_offer), len(ordinary))):
        for bucket in (public_offer, ordinary):
            if index >= len(bucket):
                continue
            item = bucket[index]
            identity = str(item["external_id"])
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(item)
            if len(selected) >= details:
                return selected
    return selected[:details]


def _probe_details(
    session: requests.Session,
    candidates: list[dict[str, Any]],
    *,
    details: int,
    pause: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected = _select_detail_candidates(candidates, details)
    results: list[dict[str, Any]] = []
    counts = Counter()
    for index, item in enumerate(selected):
        if index:
            time.sleep(pause)
        external_id = str(item["external_id"])
        url = detail_request_url(external_id)
        record: dict[str, Any] = {
            "external_id": external_id,
            "trade_number": item.get("trade_number"),
            "property_hint": item.get("property_hint"),
            "category_hint": item.get("category_hint"),
            "listing_initial_price_verified": item.get("listing_initial_price") is not None,
            "listing_status": item.get("listing_status"),
            "listing_lifecycle": item.get("listing_lifecycle"),
            "origin": item.get("origin"),
            "detail_endpoint": "/etp/trade/inner-view-lots.html",
        }
        try:
            response = _get(session, url)
            detail = parse_detail(
                PROFILE, _body(response),
                detail_url_value=url,
                external_id=external_id,
            )
        except Exception as exc:
            record.update({
                "verified": False,
                "error_type": type(exc).__name__,
                "error_code": str(exc)[:100] if isinstance(exc, ValueError) else None,
            })
            counts["failed"] += 1
            results.append(record)
            continue

        counts["verified"] += 1
        if item.get("listing_initial_price") is not None:
            counts["listing_initial_price"] += 1
        if item.get("listing_status") not in {None, "unknown"}:
            counts["listing_status"] += 1
        fields = {
            "initial_price": detail.start_price is not None,
            "current_price": detail.current_price is not None,
            "status": detail.auction_status != "unknown",
            "address": detail.address is not None,
            "cadastre": detail.cadastral_number is not None,
            "area": detail.area is not None,
            "classification": detail.category != "other",
            "application_start": detail.application_start_at is not None,
            "application_deadline": detail.application_deadline is not None,
            "auction_at": detail.auction_at is not None,
        }
        for key, present in fields.items():
            if present:
                counts[key] += 1
        if detail.raw_data.get("source_lifecycle") == "cancelled":
            counts["cancelled"] += 1
        elif detail.raw_data.get("source_lifecycle") == "finished":
            counts["finished"] += 1
        elif detail.raw_data.get("source_lifecycle") == "active":
            counts["active"] += 1
        if "ОТПП" in str(item.get("trade_number") or ""):
            counts["public_offer"] += 1

        record.update({
            "verified": True,
            "category": detail.category,
            "auction_status": detail.auction_status,
            "source_lifecycle": detail.raw_data.get("source_lifecycle"),
            "has_initial_price": fields["initial_price"],
            "has_current_price": fields["current_price"],
            "has_listing_initial_price": item.get("listing_initial_price") is not None,
            "has_address": fields["address"],
            "has_cadastre": fields["cadastre"],
            "has_area": fields["area"],
            "has_application_start": fields["application_start"],
            "has_application_deadline": fields["application_deadline"],
            "has_auction_at": fields["auction_at"],
        })
        results.append(record)
    return results, dict(counts)


def run(*, pause: float, details: int) -> dict[str, Any]:
    if pause < 0.2 or not 1 <= details <= 50:
        raise ValueError("unsafe_audit_limits")

    session = requests.Session()
    first, observations, lots, last_body, first_body = _scan_pass(
        session, pause=pause, pass_no=1
    )
    time.sleep(pause)
    second, observations2, lots2, _, _ = _scan_pass(
        session, pause=pause, pass_no=2
    )

    last_page = int(first["declared_last_page"])
    time.sleep(pause)
    overflow = _get(session, LISTING_URL, params=catalogue_params(last_page + 1))
    terminal_ok, terminal_reason = terminal_page_contract(
        last_body, _body(overflow), last_page=last_page
    )
    status_evidence = _status_filter_evidence(
        session, first_body, pause=pause
    )

    discovered, discovery_evidence = _discover_unresolved_lots(
        session, observations, pause=pause
    )
    candidates = _detail_candidates(lots, observations, discovered)
    detail_results, detail_counts = _probe_details(
        session, candidates, details=details, pause=pause
    )

    ids1 = {lot.external_id for lot in lots}
    ids2 = {lot.external_id for lot in lots2}
    return {
        "mode": "readonly_aukcioncenter_source_contract",
        "catalogue_url": LISTING_URL,
        "active_filter": catalogue_params(1),
        "production_database_writes": 0,
        "archival_allowed": False,
        "passes": [first, second],
        "two_pass_digest_equal": first["digest"] == second["digest"],
        "two_pass_identity_set_equal": ids1 == ids2,
        "two_pass_identity_added": sorted(ids2 - ids1)[:50],
        "two_pass_identity_removed": sorted(ids1 - ids2)[:50],
        "status_filter_evidence": status_evidence,
        "terminal_probe": {
            "requested_page": last_page + 1,
            "proven": terminal_ok,
            "reason": terminal_reason,
            "overflow_declared_pages": list(declared_pages(_body(overflow))),
        },
        "unresolved_lot_index_evidence": discovery_evidence,
        "detail_candidate_count": len(candidates),
        "individual_details_requested": min(details, len(candidates)),
        "individual_details_checked": len(detail_results),
        "detail_counts": detail_counts,
        "details": detail_results,
        "production_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(pause=args.pause, details=args.details)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
