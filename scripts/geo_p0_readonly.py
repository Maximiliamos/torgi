"""Read-only P0 census of unmapped lots. No network requests or database writes.

Run on an authorized database-bearing machine:
  python scripts/geo_p0_readonly.py
  # Windows Docker: Get-Content -Raw -Encoding UTF8 .\scripts\geo_p0_readonly.py |
  #   docker exec -i bankrotai-home-geocoding-worker python -

The script emits only aggregate metrics and bounded numeric lot-ID samples.
An extracted address/cadastral candidate is NOT proof of correct geocoding.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import re
import sys
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, or_, select, text

from bankrotai.db import GeoFailure, ProcessedLot, SessionLocal
from bankrotai.extractors import extract_address, extract_cadastral_numbers
from bankrotai.regions import REGIONS_BY_CODE


_ACTIVE = frozenset({"active", "scheduled"})
_OPERATIONAL = frozenset({
    "dns_error", "connect_timeout", "read_timeout", "connection_error",
    "tls_error", "rate_limited", "provider_5xx", "provider_protocol",
    "circuit_open",
})
_CADASTRE = re.compile(r"^\d{2}:\d{1,2}:\d{5,9}:\d{1,12}$")


def input_type(address: Any, cadastral: Any) -> str:
    has_address = bool(str(address or "").strip())
    has_cadastre = bool(str(cadastral or "").strip())
    if has_address and has_cadastre:
        return "cadastre_and_address"
    if has_cadastre:
        return "cadastre_only"
    if has_address:
        return "address_only"
    return "no_stored_input"


def safe_public_url(value: Any) -> str | None:
    """Keep only bounded public HTTP(S) URLs; never emit credentials/fragments."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))[:500]


def classify_failure(status: Any, message: Any) -> tuple[str, list[str], list[str]]:
    """Classify stored evidence; do not infer provider outcomes from a no-match."""
    status = str(status or "")
    try:
        payload = json.loads(str(message or "")) if message else {}
    except (ValueError, TypeError):
        payload = {}
    attempts = payload.get("attempts") if isinstance(payload, dict) else []
    attempts = attempts if isinstance(attempts, list) else []
    providers: list[str] = []
    reasons: list[str] = []
    operational = False
    mismatch = False
    for row in attempts:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("source") or "")
        reason = str(row.get("reason") or "")
        if provider:
            providers.append(provider[:60])
        if reason:
            reasons.append(reason[:60])
        operational |= bool(row.get("operational")) or reason in _OPERATIONAL
        mismatch |= reason.endswith("_mismatch") or reason in {
            "low_confidence", "coordinates_out_of_range",
        }
    if status == "network_wait" or operational:
        return "provider_operational", providers, reasons
    if status == "deferred_validation" or mismatch:
        return "validation_rejected", providers, reasons
    if status == "terminal":
        return "terminal_internal", providers, reasons
    if status == "deferred_no_match":
        return "unconfirmed_provider_no_match", providers, reasons
    if status in {"queued", "p7_queued"}:
        return "queued", providers, reasons
    if not status:
        return "unattempted_or_no_failure_record", providers, reasons
    return "other_state", providers, reasons


def primary_cause(
    failure: str, stored_input: str, *, extracted_address: bool, extracted_cadastre: bool,
) -> str:
    if failure in {"provider_operational", "validation_rejected", "terminal_internal"}:
        return failure
    if stored_input == "no_stored_input":
        if extracted_address or extracted_cadastre:
            return "source_extraction_gap_candidate"
        return "source_detail_required"
    if stored_input == "cadastre_only" and extracted_address:
        return "source_address_extraction_gap_candidate"
    if failure == "unconfirmed_provider_no_match":
        return failure
    return failure


def summarize_rows(rows: Iterable[Any]) -> dict[str, Any]:
    """Stream rows, never return titles, addresses, payloads or credentials."""
    total = 0
    active = 0
    by_input: Counter[str] = Counter()
    by_state: Counter[str] = Counter()
    by_cause: Counter[str] = Counter()
    by_source: dict[str, Any] = defaultdict(lambda: {
        "total": 0, "active": 0, "by_cause": Counter(), "by_input": Counter(),
        "by_failure": Counter(), "extractable_address_candidates": 0,
    })
    by_region: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_provider: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    sample_ids: dict[str, list[int]] = defaultdict(list)
    sample_ids_by_source_cause: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    sample_refs_by_source_cause: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        (
            lot_id, source, external_id, lot_url, source_url,
            region, region_name, status, address, cadastre, title, description,
            failure_status, failure_message,
        ) = row
        total += 1
        status = str(status or "unknown")
        active_now = status in _ACTIVE
        if active_now:
            active += 1
        by_status[status] += 1
        by_region[str(region or "missing")] += 1
        inp = input_type(address, cadastre)
        by_input[inp] += 1
        failure, providers, reasons = classify_failure(failure_status, failure_message)
        by_state[failure] += 1
        by_provider.update(set(providers))
        by_reason.update(set(reasons))
        title_text = str(title or "")[:1200]
        description_text = str(description or "")[:6000]
        # Evidence signals, not validated real-estate locations.
        extracted = extract_address(title_text) or extract_address(description_text)
        extracted_cads = extract_cadastral_numbers(
            (title_text + " " + description_text)[:7200],
        )
        cause = primary_cause(
            failure, inp, extracted_address=bool(extracted),
            extracted_cadastre=bool(extracted_cads),
        )
        if active_now:
            by_cause[cause] += 1
        item = by_source[str(source or "unknown")]
        item["total"] += 1
        item["active"] += int(active_now)
        item["by_input"][inp] += 1
        item["by_failure"][failure] += 1
        if active_now:
            item["by_cause"][cause] += 1
        if extracted:
            item["extractable_address_candidates"] += 1
        if inp in {"cadastre_only", "no_stored_input"} and extracted:
            quality["missing_stored_address_with_asset_text_signal"] += 1
        cad_text = str(cadastre or "").strip()
        if _CADASTRE.fullmatch(cad_text):
            cad_region = cad_text[:2]
            canonical = str(region or "").zfill(2) if region else ""
            if not canonical and cad_region in REGIONS_BY_CODE:
                quality["missing_region_but_cadastral_prefix_known"] += 1
            elif canonical and cad_region in REGIONS_BY_CODE and canonical != cad_region:
                quality["region_cadastral_prefix_mismatch"] += 1
        if active_now and len(sample_ids[cause]) < 12:
            sample_ids[cause].append(int(lot_id))
        if active_now:
            source_key = str(source or "unknown")
            source_samples = sample_ids_by_source_cause[source_key][cause]
            source_refs = sample_refs_by_source_cause[source_key][cause]
            if len(source_samples) < 5:
                source_samples.append(int(lot_id))
                source_refs.append({
                    "lot_id": int(lot_id),
                    "external_id": str(external_id or "")[:160],
                    "public_url": safe_public_url(lot_url) or safe_public_url(source_url),
                })
    return {
        "nonarchived_unduplicated_unmapped": total,
        "active_or_scheduled_unmapped": active,
        "nonactive_unmapped": total - active,
        "by_auction_status": dict(by_status.most_common()),
        "by_input": dict(by_input.most_common()),
        "failure_classes_all_nonarchived": dict(by_state.most_common()),
        "primary_causes_active_exclusive": dict(by_cause.most_common()),
        "by_source": {
            key: {
                "total": val["total"], "active": val["active"],
                "by_cause": dict(val["by_cause"].most_common()),
                "by_input": dict(val["by_input"].most_common()),
                "by_failure": dict(val["by_failure"].most_common()),
                "extractable_address_candidates": val["extractable_address_candidates"],
            }
            for key, val in sorted(by_source.items())
        },
        "by_region": dict(by_region.most_common()),
        "provider_attempt_sources": dict(by_provider.most_common()),
        "attempt_reasons": dict(by_reason.most_common()),
        "quality_signals": dict(quality.most_common()),
        "sample_lot_ids_by_cause": dict(sample_ids),
        "sample_lot_ids_by_source_and_cause": {
            source: {cause: ids for cause, ids in sorted(causes.items())}
            for source, causes in sorted(sample_ids_by_source_cause.items())
        },
        "sample_public_refs_by_source_and_cause": {
            source: {cause: refs for cause, refs in sorted(causes.items())}
            for source, causes in sorted(sample_refs_by_source_cause.items())
        },
        "crosscheck_primary_cause_total": sum(by_cause.values()),
    }


def run() -> dict[str, Any]:
    with SessionLocal() as session:
        # Defense in depth. PostgreSQL will reject accidental writes even if
        # a future refactor introduces any; SQLite has no comparable mode.
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        current_missing = or_(
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
        )
        scope = (
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.duplicate_of_id.is_(None),
        )
        active_scope = (*scope, ProcessedLot.auction_status.in_(tuple(_ACTIVE)))
        eligible_active = session.scalar(
            select(func.count()).select_from(ProcessedLot).where(*active_scope),
        )
        mapped_active = session.scalar(
            select(func.count()).select_from(ProcessedLot).where(
                *active_scope,
                ProcessedLot.current_geo_lat.is_not(None),
                ProcessedLot.current_geo_lon.is_not(None),
            ),
        )
        rows = session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.source_system,
                ProcessedLot.external_id,
                ProcessedLot.lot_url,
                ProcessedLot.source_url,
                ProcessedLot.region_code,
                ProcessedLot.region_name,
                ProcessedLot.auction_status,
                ProcessedLot.address,
                ProcessedLot.cadastral_number,
                ProcessedLot.title,
                ProcessedLot.description,
                GeoFailure.status,
                GeoFailure.error_message,
            )
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(*scope, current_missing)
            .order_by(ProcessedLot.id)
            .execution_options(stream_results=True, yield_per=250)
        )
        result = summarize_rows(rows)
        result["eligible_active"] = int(eligible_active or 0)
        result["mapped_active"] = int(mapped_active or 0)
        result["active_coverage_percent"] = (
            round(100 * int(mapped_active or 0) / int(eligible_active), 2)
            if eligible_active else None
        )
        assert result["active_or_scheduled_unmapped"] == (
            int(eligible_active or 0) - int(mapped_active or 0)
        ), "Active population mismatch; do not accept incomplete GEO census"
        assert result["crosscheck_primary_cause_total"] == result["active_or_scheduled_unmapped"]
        session.rollback()  # explicitly end read-only transaction
    return {
        "report": "geo_p0_census",
        "schema_version": "3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "no_provider_requests": True,
        "no_raw_addresses_or_contact_data": True,
        "cause_note": "Evidence signals and previous failure classes, not proof of causal repair",
        **result,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(run(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(
            json.dumps({"report": "geo_p0_census", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
