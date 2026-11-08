"""Reusable, fail-closed detail-cache helpers for complete catalogue scans.

Catalogue discovery stays exhaustive. Expensive detail fetches may be skipped only
when an existing source row carries enough property evidence and the live catalogue
still agrees on mutable fields that the catalogue exposes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

DETAIL_CACHE_VERSION = 1
DEFAULT_DETAIL_MAX_AGE = timedelta(days=7)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def stable_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def money(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def catalogue_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = {
        str(key): payload[key]
        for key in sorted(payload)
    }
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_from_source_row(row: Any) -> dict[str, Any]:
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    verified = parse_timestamp(raw.get("detail_verified_at"))
    # last_seen_at proves only catalogue observation, not independent detail
    # verification. Legacy rows without explicit proof must be refetched.
    bootstrap = verified
    return {
        "external_id": str(row.external_id),
        "title": row.title,
        "start_price": str(row.start_price) if row.start_price is not None else None,
        "current_price": str(row.current_price) if row.current_price is not None else None,
        "source_status": row.source_status,
        "category": row.category,
        "cadastral_number": row.cadastral_number,
        "lot_url": row.lot_url or row.source_url,
        "is_archived": bool(row.is_archived),
        "detail_verified_at": verified.isoformat() if verified else None,
        "bootstrap_verified_at": bootstrap.isoformat() if bootstrap else None,
        "catalogue_fingerprint": raw.get("catalogue_fingerprint"),
    }


def snapshot_reusable(
    snapshot: Mapping[str, Any] | None,
    *,
    expected_price: object,
    expected_status: str,
    expected_title: object | None = None,
    fingerprint: str | None = None,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_DETAIL_MAX_AGE,
) -> bool:
    if not snapshot or snapshot.get("is_archived"):
        return False
    if stable_text(snapshot.get("category")) == "land":
        return False
    if not snapshot.get("cadastral_number") or not snapshot.get("lot_url"):
        return False
    # A legacy bootstrap timestamp cannot substitute for detail provenance.
    observed_at = parse_timestamp(snapshot.get("detail_verified_at"))
    current = _aware(now or datetime.now(timezone.utc))
    if observed_at is None or observed_at > current or current - observed_at > max_age:
        return False
    if stable_text(snapshot.get("source_status")) != stable_text(expected_status):
        return False
    wanted_price = money(expected_price)
    if wanted_price is None:
        return False
    known_prices = {
        value
        for value in (
            money(snapshot.get("start_price")),
            money(snapshot.get("current_price")),
        )
        if value is not None
    }
    if wanted_price not in known_prices:
        return False
    if expected_title is not None and stable_text(snapshot.get("title")) != stable_text(expected_title):
        return False
    prior_fingerprint = snapshot.get("catalogue_fingerprint")
    if prior_fingerprint and fingerprint and prior_fingerprint != fingerprint:
        return False
    return True
