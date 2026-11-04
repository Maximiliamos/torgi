"""Double-pass, READ-ONLY evidence for source-by-source ETP onboarding.

This is not an import and never approves a source for production. A positive
catalogue count or absence of next_cursor is not proof of reaching the real
last page. Keep operator-reviewed acceptance separate from these observations.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from bankrotai.connectors.etp_sources import EtpSearchFilters, PROFILES, SOURCE_CONNECTOR_TYPES

MAX_AUDIT_PAGES = 3500
MAX_DETAIL_PROBES = 30
_ALLOWED_STATUSES = frozenset({"scheduled", "active", "closed", "unknown"})


def _end_evidence(metadata: dict[str, Any], pages: int) -> str | None:
    if metadata.get("source_end_verified") is True:
        return "source_end_verified"
    total = metadata.get("total_pages")
    observed = metadata.get("progress_current")
    if type(total) is int and total > 0 and type(observed) is int and observed == total == pages:
        return "declared_total_pages"
    return None


async def _scan(
    source_id: str,
    connector: Any,
    filters: Any,
    *,
    max_pages: int,
    delay_seconds: float,
    max_detail_probes: int,
) -> dict[str, Any]:
    seen: set[str] = set()
    cursors: set[str] = set()
    unknown_status = 0
    missing_price = 0
    observed_detail = 0
    checked_detail = 0
    detail_errors: list[str] = []
    latest_meta: dict[str, Any] = {}
    page_no = 0
    cursor: str | None = None
    end_evidence: str | None = None
    while page_no < max_pages:
        if cursor is not None:
            if cursor in cursors:
                raise ValueError("repeated_cursor")
            cursors.add(cursor)
        page = await connector.search(filters, cursor)
        page_no += 1
        meta = page.metadata if isinstance(page.metadata, dict) else {}
        latest_meta = meta
        items = tuple(page.items)
        if not items and page_no == 1:
            raise ValueError("empty_first_page_without_verified_end")
        if not items and page.next_cursor:
            raise ValueError("empty_page_with_next_cursor")
        for lot in items:
            identity = str(getattr(lot, "external_id", "") or "").strip()
            if not identity or getattr(lot, "source_system", "") != source_id:
                raise ValueError("missing_or_wrong_source_lot_id")
            if identity in seen:
                raise ValueError("duplicate_lot_across_pages")
            seen.add(identity)
            status = str(getattr(lot, "auction_status", "") or "").lower()
            if status not in _ALLOWED_STATUSES:
                raise ValueError("invalid_status_enum")
            unknown_status += status == "unknown"
            start_price = getattr(lot, "start_price", None)
            current_price = getattr(lot, "current_price", None)
            if start_price is None and current_price is None:
                missing_price += 1
            if (start_price is not None and float(start_price) < 0) or (
                current_price is not None and float(current_price) < 0
            ):
                raise ValueError("negative_source_price")
            verified_in_listing = getattr(lot, "detail_level", "") == "detail"
            if verified_in_listing:
                observed_detail += 1
            if checked_detail >= max_detail_probes:
                continue
            # A listing alone is never enough to certify independent lot price
            # or identity. Try exact detail only when the connector advertises
            # that it can fetch it. Log only failure categories, not PII.
            if not verified_in_listing and "detail_enrichment" in getattr(connector, "capabilities", ()):
                try:
                    detail = await connector.fetch_lot(identity)
                    if (detail.external_id != identity or detail.source_system != source_id
                            or detail.detail_level != "detail"):
                        raise ValueError("detail_identity_or_type_mismatch")
                    if (start_price is not None and detail.start_price is not None
                            and abs(float(start_price) - float(detail.start_price)) > 0.01):
                        raise ValueError("listing_detail_initial_price_mismatch")
                    checked_detail += 1
                except Exception as exc:
                    detail_errors.append(type(exc).__name__)
            elif verified_in_listing:
                checked_detail += 1
        if page.next_cursor is None:
            end_evidence = _end_evidence(latest_meta, page_no)
            break
        cursor = str(page.next_cursor)
        await asyncio.sleep(delay_seconds)
    else:
        raise ValueError("page_cap_hit_before_proven_source_end")
    if not seen:
        raise ValueError("no_verified_lot_ids")
    digest = hashlib.sha256(
        (source_id + "\n" + "\n".join(sorted(seen))).encode("utf-8")
    ).hexdigest()
    return {
        "source": source_id, "pages": page_no, "observed_lot_ids": len(seen),
        "identity_sha256": digest, "terminal_evidence": end_evidence,
        "all_lot_ids_have_source": True, "details_observed_in_search": observed_detail,
        "details_checked": checked_detail, "detail_errors": detail_errors[:15],
        "unknown_status_count": unknown_status,
        "missing_price_count": missing_price,
        "catalogue_total_changes": latest_meta.get("catalogue_total_changes"),
        "database_writes": 0, "map_writes": 0,
    }


async def audit_two_passes(
    source_id: str,
    factory: Callable[[], Any],
    filters: Any,
    *,
    max_pages: int = 3500,
    max_detail_probes: int = 30,
    delay_seconds: float = 0.5,
) -> dict[str, Any]:
    if not 1 <= max_pages <= MAX_AUDIT_PAGES:
        raise ValueError("invalid_page_limit")
    if not 0 <= max_detail_probes <= MAX_DETAIL_PROBES:
        raise ValueError("invalid_detail_limit")
    if not 0.5 <= delay_seconds <= 10:
        raise ValueError("unsafe_request_cadence")
    reports = []
    for pass_num in (1, 2):
        connector = factory()
        try:
            result = await _scan(
                source_id, connector, filters, max_pages=max_pages,
                max_detail_probes=max_detail_probes, delay_seconds=delay_seconds,
            )
            result["pass"] = pass_num
            reports.append(result)
        except Exception as exc:
            return {
                "source": source_id, "status": "blocked",
                "blocker": type(exc).__name__,
                "two_complete_passes": False,
                "completed_passes": len(reports),
                "database_writes": 0, "map_writes": 0,
                "production_approved": False,
            }
        finally:
            session = getattr(connector, "session", None)
            close = getattr(session, "close", None)
            if callable(close):
                close()
    identical = reports[0]["identity_sha256"] == reports[1]["identity_sha256"]
    all_terminal = all(r["terminal_evidence"] for r in reports)
    all_detail = all(
        r["details_checked"] >= min(r["observed_lot_ids"], max_detail_probes)
        and not r["detail_errors"] for r in reports
    ) if max_detail_probes > 0 else False
    eligible_for_review = bool(identical and all_terminal and all_detail)
    reasons = []
    if not identical:
        reasons.append("identity_set_changed_between_passes")
    if not all_terminal:
        reasons.append("last_page_not_independently_proven")
    if not all_detail:
        reasons.append("insufficient_verified_individual_details")
    return {
        "source": source_id, "audit_timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "review_required" if eligible_for_review else "blocked",
        "two_complete_passes": bool(all_terminal and identical),
        "stable_identity_set": identical,
        "detail_sample_verified": all_detail,
        "reasons": reasons,
        "passes": reports,
        "database_writes": 0, "map_writes": 0,
        # Even perfect crawler observations do NOT certify sale-only semantics,
        # cross-source collisions, map GEO or an explicit operator release.
        "production_approved": False,
    }


def _provider(source_id: str) -> tuple[Callable[[], Any], Any]:
    profiles = {p.source_id: p for p in PROFILES}
    if source_id not in profiles:
        raise ValueError("unknown_direct_etp")
    if profiles[source_id].blocked_reason:
        raise ValueError("blocked_direct_etp")
    if source_id == "cdtrf.ru":
        import requests
        from bankrotai.connectors.registry.cdtrf_sync import CdtrfSourceCrawler, CdtrfSyncFilters
        return (lambda: CdtrfSourceCrawler(requests.Session())), CdtrfSyncFilters()
    return SOURCE_CONNECTOR_TYPES[source_id], EtpSearchFilters(page=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--max-pages", type=int, default=3500)
    parser.add_argument("--max-detail-probes", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    args = parser.parse_args()
    try:
        factory, filters = _provider(args.source)
        result = asyncio.run(audit_two_passes(
            args.source, factory, filters, max_pages=args.max_pages,
            max_detail_probes=args.max_detail_probes,
            delay_seconds=args.delay_seconds,
        ))
    except Exception as exc:
        result = {
            "source": args.source, "status": "blocked",
            "blocker": type(exc).__name__, "production_approved": False,
            "database_writes": 0, "map_writes": 0,
        }
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
