"""Catalogue fingerprints used to avoid redundant heavy detail requests.

The helpers are deliberately source-local and fail closed: a detail is reused
only when the current catalogue evidence matches a previously verified detail
and that evidence is still fresh enough for the scheduled incremental run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import SourceLot, utc_now


CATALOGUE_FINGERPRINT_VERSION = 1
DEFAULT_DETAIL_MAX_AGE = timedelta(days=7)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount.quantize(Decimal("0.01"))


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _naive_utc(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _naive_utc(parsed)


def _stable_payload(source_system: str, candidate: dict[str, Any]) -> dict[str, Any]:
    if source_system == "m-ets.ru":
        return {
            "external_id": str(candidate.get("external_id") or ""),
            "catalogue_price": str(_money(candidate.get("catalogue_price"))),
            "catalogue_status": _clean(candidate.get("catalogue_status")).casefold(),
            "title": _clean(candidate.get("title")).casefold(),
        }
    if source_system == "cdtrf.ru":
        return {
            "external_id": str(candidate.get("external_id") or ""),
            "trade_id": str(candidate.get("trade_id") or ""),
            "trade_lot_id": str(candidate.get("trade_lot_id") or ""),
            "property_category_ids": sorted(
                int(value) for value in candidate.get("property_category_ids", [])
            ),
            "region_id": candidate.get("region_id"),
            "source_status_text": _clean(candidate.get("source_status_text")).casefold(),
            "listing_price_rub": str(_money(candidate.get("listing_price_rub"))),
            "request_time_begin": _clean(candidate.get("request_time_begin")),
            "request_time_end": _clean(candidate.get("request_time_end")),
        }
    raise ValueError(f"Unsupported catalogue fingerprint source: {source_system}")


def catalogue_candidate_fingerprint(
    source_system: str,
    candidate: dict[str, Any],
) -> str:
    payload = _stable_payload(source_system, candidate)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verified_detail_timestamp(row: SourceLot) -> datetime | None:
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    explicit = _iso_datetime(raw.get("detail_verified_at"))
    if explicit is not None:
        return explicit

    # Backward-compatible bootstrap for rows produced by the previous
    # catalogue->detail implementation.  last_seen_at is trustworthy here only
    # when the row contains the source-specific detail verification markers.
    if row.source_system == "m-ets.ru":
        verified = bool(
            raw.get("catalogue_price_verified")
            and raw.get("catalogue_status_verified")
            and row.cadastral_number
        )
    elif row.source_system == "cdtrf.ru":
        verified = bool(
            raw.get("evidence") == "url_html_nuxt_identity_match"
            and raw.get("category_evidence") == "lotInfo_and_official_badge"
            and row.cadastral_number
        )
    else:
        verified = False
    return _naive_utc(row.last_seen_at) if verified else None


def _cdtrf_catalogue_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _candidate_matches_legacy_detail(
    source_system: str,
    candidate: dict[str, Any],
    row: SourceLot,
) -> bool:
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    if source_system == "m-ets.ru":
        status = _clean(candidate.get("catalogue_status")).casefold()
        expected_status = (
            "active"
            if "приема заявок" in status
            else "scheduled"
            if "объявлен" in status
            else None
        )
        price = _money(candidate.get("catalogue_price"))
        stored_prices = {
            value
            for value in (_money(row.start_price), _money(row.current_price))
            if value is not None
        }
        return bool(
            expected_status
            and row.source_status == expected_status
            and price is not None
            and price in stored_prices
            and raw.get("catalogue_price_verified")
            and raw.get("catalogue_status_verified")
            and row.cadastral_number
        )
    if source_system == "cdtrf.ru":
        price = _money(candidate.get("listing_price_rub"))
        stored_prices = {
            value
            for value in (_money(row.start_price), _money(row.current_price))
            if value is not None
        }
        begin = _cdtrf_catalogue_time(candidate.get("request_time_begin"))
        end = _cdtrf_catalogue_time(candidate.get("request_time_end"))
        return bool(
            raw.get("evidence") == "url_html_nuxt_identity_match"
            and raw.get("category_evidence") == "lotInfo_and_official_badge"
            and str(raw.get("trade_id") or "") == str(candidate.get("trade_id") or "")
            and str(raw.get("trade_lot_id") or "") == str(candidate.get("trade_lot_id") or "")
            and _clean(raw.get("source_status_text")).casefold()
            == _clean(candidate.get("source_status_text")).casefold()
            and price is not None
            and price in stored_prices
            and begin is not None
            and end is not None
            and _naive_utc(row.application_start_at) == begin
            and _naive_utc(row.application_deadline) == end
            and row.cadastral_number
        )
    return False


def select_catalogue_candidates(
    session: Session,
    source_system: str,
    candidates: list[dict[str, Any]],
    *,
    max_age: timedelta = DEFAULT_DETAIL_MAX_AGE,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a per-ID decision used by M-ETS/CDTRF detail workers.

    Unknown rows, changed catalogue evidence, stale details and malformed
    candidates always require a fresh detail request.
    """
    if source_system not in {"m-ets.ru", "cdtrf.ru"}:
        raise ValueError(f"Unsupported changed-detail source: {source_system}")
    current = _naive_utc(now or utc_now()) or utc_now()
    external_ids = [
        str(candidate.get("external_id") or "")
        for candidate in candidates
        if candidate.get("external_id")
    ]
    rows = {
        row.external_id: row
        for row in session.scalars(
            select(SourceLot).where(
                SourceLot.source_system == source_system,
                SourceLot.external_id.in_(external_ids),
            )
        ).all()
    } if external_ids else {}

    decisions: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        external_id = str(candidate.get("external_id") or "")
        if not external_id:
            continue
        fingerprint = catalogue_candidate_fingerprint(source_system, candidate)
        row = rows.get(external_id)
        skip = False
        if row is not None and row.processed_lot_id is not None:
            raw = row.raw_data if isinstance(row.raw_data, dict) else {}
            verified_at = _verified_detail_timestamp(row)
            fresh = bool(
                verified_at is not None
                and timedelta(0) <= current - verified_at <= max_age
            )
            same_fingerprint = (
                raw.get("catalogue_fingerprint_version")
                == CATALOGUE_FINGERPRINT_VERSION
                and raw.get("catalogue_fingerprint") == fingerprint
            )
            legacy_match = (
                raw.get("catalogue_fingerprint") is None
                and _candidate_matches_legacy_detail(source_system, candidate, row)
            )
            skip = bool(fresh and (same_fingerprint or legacy_match))
        decisions[external_id] = {
            "skip_detail": skip,
            "fingerprint": fingerprint,
        }
    return decisions


def mark_verified_detail(
    lot: Any,
    fingerprint: str,
    *,
    observed_at: datetime | None = None,
) -> None:
    raw = dict(lot.raw_data or {})
    raw.update(
        {
            "catalogue_fingerprint": fingerprint,
            "catalogue_fingerprint_version": CATALOGUE_FINGERPRINT_VERSION,
            "detail_verified_at": (observed_at or utc_now()).isoformat(),
        }
    )
    lot.raw_data = raw


def touch_unchanged_catalogue_rows(
    session: Session,
    source_system: str,
    decisions: list[dict[str, str]],
    *,
    run_id: str,
    observed_at: datetime | None = None,
) -> int:
    """Advance liveness metadata for catalogue rows whose detail was reused."""
    if not decisions:
        return 0
    current = observed_at or utc_now()
    by_id = {
        str(item["external_id"]): str(item["fingerprint"])
        for item in decisions
        if item.get("external_id") and item.get("fingerprint")
    }
    rows = session.scalars(
        select(SourceLot).where(
            SourceLot.source_system == source_system,
            SourceLot.external_id.in_(list(by_id)),
        )
    ).all()
    touched = 0
    for row in rows:
        fingerprint = by_id.get(row.external_id)
        if fingerprint is None or row.processed_lot_id is None:
            continue
        raw = dict(row.raw_data or {})
        raw["catalogue_fingerprint"] = fingerprint
        raw["catalogue_fingerprint_version"] = CATALOGUE_FINGERPRINT_VERSION
        raw.setdefault("detail_verified_at", row.last_seen_at.isoformat())
        row.raw_data = raw
        row.last_seen_at = current
        row.last_sync_run_id = run_id
        row.missing_successful_runs = 0
        touched += 1
    return touched
