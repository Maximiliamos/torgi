"""Read-only stage-by-stage diagnostics for all 22 registered ETP sources.

This is a diagnostic, NOT a catalogue acceptance, publication or ingestion.
A two-page sample cannot prove the last page; a listing cannot prove a detail.
Only first-party connector requests are used. No browser anti-bot bypass.
Do not log provider HTML, titles, debtor names, query strings or credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from bankrotai.connectors.etp_sources import (
    EtpSearchFilters, EtpSourceProfile, PROFILES as DIRECT_PROFILES, SOURCE_CONNECTOR_TYPES,
)

CHECKS = (
    "catalogue_url", "transport", "catalogue_markup", "lot_discovery",
    "stable_lot_ids", "pagination_next", "last_page_proven",
    "individual_detail", "detail_identity", "initial_or_current_price",
    "auction_status", "property_classification", "cookie_or_tutorial_modal",
    "production_import", "geocoding", "map_publication", "scheduler",
)
NOT_TESTED = "NOT_TESTED"
PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_APPLICABLE = "N/A"


def _entry(state: str = NOT_TESTED, *, reason: str | None = None,
           page: int | None = None, count: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"state": state}
    if reason:
        result["reason"] = reason[:120]
    if page is not None:
        result["page"] = page
    if count is not None:
        result["count"] = count
    return result


def _http_status(exc: Exception) -> int | None:
    """Extract only a safe HTTP code, never a URL, header or response body."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        return status
    if type(exc).__name__ not in {"SourceAccessError", "HTTPError"}:
        return None
    match = re.search(r"\\bHTTP\\s+([1-5]\\d{2})\\b", str(exc))
    return int(match.group(1)) if match else None


def _error_kind(exc: Exception) -> tuple[str, str]:
    """Diagnostic categories are allowlisted; never expose provider text."""
    name = type(exc).__name__
    if name == "ConnectorCapabilityError":
        return NOT_APPLICABLE, "unsupported_connector_capability"
    if name in {"SSLError", "SSLCertVerificationError"}:
        return BLOCKED, "tls_certificate_validation"
    if name in {"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout"}:
        return BLOCKED, "transport_" + name
    if name in {"SourceAccessError", "HTTPError", "TooManyRedirects"}:
        status = _http_status(exc)
        return BLOCKED, f"provider_http_{status}" if status is not None else "provider_access_or_http"
    if name in {"SourceSchemaError", "ValueError", "KeyError", "JSONDecodeError"}:
        return FAIL, "source_schema_or_page_contract"
    return FAIL, "unexpected_" + name[:60]


def _safe_catalogue_host(url: str) -> str | None:
    try:
        p = urlsplit(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.port:
            return None
        return p.hostname
    except ValueError:
        return None


def _definitions() -> list[tuple[str, str, Any]]:
    profiles: list[tuple[str, str, EtpSourceProfile | None]] = [
        (p.source_id, p.listing_url, p) for p in DIRECT_PROFILES
    ]
    # The 22-source inventory includes Lot-online as an existing first-party
    # production connector; it is deliberately not scraped by the direct ETP registry.
    profiles.append(("lot-online.ru", "https://lot-online.ru/", None))
    if len(profiles) != 22 or len({p[0] for p in profiles}) != 22:
        raise RuntimeError("unexpected_22_source_registry")
    return profiles


def source_ids() -> tuple[str, ...]:
    return tuple(s for s, _, _ in _definitions())


def _connector(source: str) -> tuple[Any, Any]:
    if source == "lot-online.ru":
        from bankrotai.connectors.registry import connector_registry
        from bankrotai.scraper_contracts import LotOnlineSearchFilters
        from bankrotai.scrapers import LotOnlineClient
        return connector_registry.create(source), LotOnlineSearchFilters(
            category_id=LotOnlineClient.DEFAULT_CATEGORY_ID,
            archive_mode="false", page=1, page_size=96,
        )
    return SOURCE_CONNECTOR_TYPES[source](timeout=20), EtpSearchFilters(page=1)


async def diagnose_source(
    source: str, *, max_pages: int = 2, detail_probes: int = 1,
    pause_seconds: float = 1.0,
) -> dict[str, Any]:
    """Small bounded live sample. Never mutate, reconcile or publish lots."""
    if not 1 <= max_pages <= 2 or not 0 <= detail_probes <= 2:
        raise ValueError("unsafe_probe_limits")
    if not 1.0 <= pause_seconds <= 5.0:
        raise ValueError("unsafe_probe_cadence")
    registry = {s: (url, p) for s, url, p in _definitions()}
    if source not in registry:
        raise ValueError("unknown_etp_source")
    url, profile = registry[source]
    stages = {key: _entry() for key in CHECKS}
    stages["catalogue_url"] = _entry(
        PASS if _safe_catalogue_host(url) else FAIL,
        reason="configured_https_host" if _safe_catalogue_host(url) else "invalid_catalogue_url",
    )
    # HTTP and API parsers don't click browser modals. Browser coverage is
    # NOT_TESTED, not an invented green check or a failure.
    stages["cookie_or_tutorial_modal"] = _entry(
        NOT_APPLICABLE, reason="http_or_api_connector_no_browser_ui"
    )
    report: dict[str, Any] = {
        "source": source, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "catalogue_host": _safe_catalogue_host(url),
        "stages": stages, "pages_checked": 0, "sample_lots": 0,
        "sample_only": True, "complete_source_inventory": False,
        "production_approved": False, "database_writes": 0,
        "map_writes": 0, "archival_enabled": False,
        "first_failure": None,
    }

    def fail(stage: str, exc: Exception, page: int) -> None:
        state, reason = _error_kind(exc)
        stages[stage] = _entry(state, reason=reason, page=page)
        status = _http_status(exc)
        if status is not None:
            stages[stage]["http_status"] = status
        stages[stage]["exception_class"] = type(exc).__name__[:60]
        if state != NOT_APPLICABLE and report["first_failure"] is None:
            report["first_failure"] = {
                "stage": stage, "page": page, "category": reason,
                **({"http_status": status} if status is not None else {}),
            }

    if stages["catalogue_url"]["state"] != PASS:
        report["first_failure"] = {"stage": "catalogue_url", "category": "invalid_url"}
        return report
    if profile is not None and profile.blocked_reason:
        stages["transport"] = _entry(BLOCKED, reason="profile_explicitly_blocked")
        report["first_failure"] = {
            "stage": "transport", "category": "profile_explicitly_blocked",
        }
        return report

    connector = None
    try:
        try:
            connector, filters = _connector(source)
        except Exception as exc:
            fail("transport", exc, 1)
            return report

        cursor: str | None = None
        seen: set[str] = set()
        sample: list[Any] = []
        next_observed = False
        for page_number in range(1, max_pages + 1):
            try:
                page = await connector.search(filters, cursor)
            except Exception as exc:
                fail("transport" if page_number == 1 else "pagination_next", exc, page_number)
                if page_number == 1 and type(exc).__name__ in {
                    "SourceSchemaError", "ValueError", "KeyError", "JSONDecodeError",
                }:
                    stages["catalogue_markup"] = stages["transport"]
                    stages["transport"] = _entry(
                        NOT_TESTED, reason="transport_success_not_independently_proven"
                    )
                    report["first_failure"]["stage"] = "catalogue_markup"
                return report
            report["pages_checked"] += 1
            stages["transport"] = _entry(PASS, reason="connector_search_returned")
            stages["catalogue_markup"] = _entry(PASS, reason="connector_parsed_catalogue")
            items = tuple(page.items)
            if not items:
                stages["lot_discovery"] = _entry(FAIL, reason="empty_page", page=page_number)
                report["first_failure"] = report["first_failure"] or {
                    "stage": "lot_discovery", "page": page_number,
                    "category": "empty_page",
                }
                break
            stages["lot_discovery"] = _entry(PASS, count=report["sample_lots"] + len(items))
            for lot in items:
                ident = str(getattr(lot, "external_id", "") or "")
                if (not ident or ident in seen or
                        getattr(lot, "source_system", None) != source):
                    stages["stable_lot_ids"] = _entry(
                        FAIL, reason="missing_duplicate_or_wrong_source_id",
                        page=page_number,
                    )
                    report["first_failure"] = report["first_failure"] or {
                        "stage": "stable_lot_ids", "page": page_number,
                        "category": "missing_duplicate_or_wrong_source_id",
                    }
                    return report
                seen.add(ident)
            stages["stable_lot_ids"] = _entry(
                PASS, reason="syntactically_unique_sample_ids", count=len(seen)
            )
            sample.extend(items[:detail_probes])
            report["sample_lots"] += len(items)
            metadata = page.metadata if isinstance(page.metadata, dict) else {}
            if metadata.get("source_end_verified") is True:
                stages["last_page_proven"] = _entry(
                    PASS, reason="connector_source_end_verified", page=page_number
                )
            elif (type(metadata.get("total_pages")) is int and
                  type(metadata.get("progress_current")) is int and
                  metadata["total_pages"] == metadata["progress_current"] == page_number):
                stages["last_page_proven"] = _entry(
                    PASS, reason="declared_total_pages", page=page_number
                )
            if not page.next_cursor:
                # A missing next cursor alone must NEVER prove the last page.
                break
            next_observed = True
            if page_number == max_pages:
                break
            cursor = str(page.next_cursor)
            if cursor == str(page_number) or cursor == "0":
                stages["pagination_next"] = _entry(
                    FAIL, reason="nonadvancing_cursor", page=page_number
                )
                report["first_failure"] = report["first_failure"] or {
                    "stage": "pagination_next", "page": page_number,
                    "category": "nonadvancing_cursor",
                }
                return report
            await asyncio.sleep(pause_seconds)
        if report["pages_checked"] > 1:
            stages["pagination_next"] = _entry(
                PASS, reason="independent_second_page_parsed",
                count=report["pages_checked"],
            )
        elif next_observed:
            stages["pagination_next"] = _entry(
                NOT_TESTED, reason="next_cursor_observed_but_not_followed"
            )
        else:
            stages["pagination_next"] = _entry(
                NOT_TESTED, reason="no_next_page_observed_no_terminal_proof"
            )
        if sample:
            price_count = sum(
                getattr(lot, "start_price", None) is not None or
                getattr(lot, "current_price", None) is not None for lot in sample
            )
            unknown = sum(
                str(getattr(lot, "auction_status", "") or "").lower() in {"", "unknown"}
                for lot in sample
            )
            categories = sum(
                str(getattr(lot, "category", "") or "").lower() not in {"", "unknown", "other"}
                for lot in sample
            )
            stages["initial_or_current_price"] = _entry(
                PASS if price_count == len(sample) else FAIL,
                reason="sample_prices_present" if price_count == len(sample)
                else "missing_individual_prices_in_sample", count=price_count,
            )
            stages["auction_status"] = _entry(
                PASS if unknown == 0 else FAIL,
                reason="sample_statuses_recognised" if unknown == 0
                else "unknown_status_in_sample", count=len(sample) - unknown,
            )
            stages["property_classification"] = _entry(
                PASS if categories == len(sample) else FAIL,
                reason="sample_categories_recognised" if categories == len(sample)
                else "unclassified_sample", count=categories,
            )
            for stage in ("initial_or_current_price", "auction_status",
                          "property_classification"):
                if stages[stage]["state"] == FAIL and report["first_failure"] is None:
                    report["first_failure"] = {
                        "stage": stage, "category": stages[stage]["reason"],
                    }
        if sample and detail_probes:
            # A listing URL is not proof of a fetched and isolated lot detail.
            details_ok = 0
            capabilities = getattr(connector, "capabilities", frozenset())
            can_fetch = "fetch_lot" in capabilities
            can_enrich = "detail_enrichment" in capabilities
            # Mature Lot-online exposes enrich_lot, not fetch_lot. This is a
            # read-only probe: it modifies the in-memory candidate only.
            if not (can_fetch or can_enrich):
                stages["individual_detail"] = _entry(
                    NOT_APPLICABLE, reason="connector_has_no_individual_detail_capability"
                )
                stages["detail_identity"] = _entry(
                    NOT_APPLICABLE, reason="no_individual_detail_capability"
                )
            else:
                for lot in sample[:detail_probes]:
                    try:
                        if can_fetch:
                            detail = await connector.fetch_lot(lot.external_id)
                        else:
                            detail = await connector.enrich_lot(lot)
                        if (detail.external_id != lot.external_id or
                                detail.source_system != source or
                                getattr(detail, "detail_level", None) != "detail"):
                            stages["detail_identity"] = _entry(
                                FAIL, reason="detail_lot_identity_mismatch"
                            )
                            report["first_failure"] = report["first_failure"] or {
                                "stage": "detail_identity",
                                "category": "detail_lot_identity_mismatch",
                            }
                            break
                        details_ok += 1
                    except Exception as exc:
                        fail("individual_detail", exc, 1)
                        break
                if details_ok:
                    stages["individual_detail"] = _entry(PASS, count=details_ok)
                    if stages["detail_identity"]["state"] == NOT_TESTED:
                        stages["detail_identity"] = _entry(PASS, count=details_ok)
    finally:
        session = getattr(connector, "session", None)
        close = getattr(session, "close", None)
        if callable(close):
            close()

    return report


async def diagnose_all(sources: tuple[str, ...], *, max_pages: int = 2,
                       detail_probes: int = 1, pause_seconds: float = 1.0) -> dict:
    reports: list[dict] = []
    for index, source in enumerate(sources, 1):
        try:
            item = await diagnose_source(
                source, max_pages=max_pages, detail_probes=detail_probes,
                pause_seconds=pause_seconds,
            )
        except Exception as exc:
            state, category = _error_kind(exc)
            item = {
                "source": source, "first_failure": {
                    "stage": "diagnostic_runner", "category": category,
                    "status": state,
                }, "stages": {key: _entry() for key in CHECKS},
                "production_approved": False, "database_writes": 0,
                "map_writes": 0, "archival_enabled": False,
            }
        reports.append(item)
        print(json.dumps({
            "progress": index, "total": len(sources),
            "source": source, "failure": item.get("first_failure"),
            "pages_checked": item.get("pages_checked", 0),
            "sample_lots": item.get("sample_lots", 0),
            "stage_states": {
                stage: entry.get("state", NOT_TESTED)
                for stage, entry in item.get("stages", {}).items()
            },
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
        if index < len(sources):
            await asyncio.sleep(pause_seconds)
    return {
        "report": "etp_stage_diagnostics_v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(reports), "sources": reports,
        "sample_only": True, "complete_source_inventory": False,
        "production_approved": False, "database_writes": 0,
        "map_writes": 0, "archival_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--source", choices=source_ids())
    parser.add_argument("--pages", type=int, choices=(1, 2), default=2)
    parser.add_argument("--detail-probes", type=int, choices=(0, 1, 2), default=1)
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    args = parser.parse_args()
    selection_ids = source_ids() if args.all else (args.source,)
    result = asyncio.run(diagnose_all(
        selection_ids, max_pages=args.pages,
        detail_probes=args.detail_probes, pause_seconds=args.pause_seconds,
    ))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
