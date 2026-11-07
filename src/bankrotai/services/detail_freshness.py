"""Conservative reusable semantic comparison and detail freshness decisions.

The input to compare_source_values is the *persisted projection* created by
batch_persistence._source_values(), not an unnormalized catalogue row.
This module has no network or database side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

# SourceLot audit/liveness metadata changes on *every* successful observation.
# Never include these in a semantic fingerprint.
AUDIT_FIELDS = frozenset({
    "id", "created_at", "first_seen_at", "last_seen_at",
    "last_sync_run_id", "missing_successful_runs", "raw_data",
    "source_updated_at",
})
# Changes to a trusted source revision affect freshness, not necessarily the
# actual business content. These raw fields are deliberately not used as the
# business fingerprint; their parsed projections ARE compared below.
VOLATILE_RAW_FIELDS = frozenset({
    "listing_fingerprint", "detail_fetched_at", "fetch_timestamp",
    "request_id", "response_time_ms", "catalogue_page",
    "detail_enrichment_status", "detail_enrichment_version",
    "source_scope", "complete_source_inventory",
})
# Only raw-only business data that has no consistently populated SourceLot
# projection is considered. Unknown raw fields are *not* silently treated as
# reliable evidence of a business change.
RAW_BUSINESS_FIELDS = (
    "photos", "photo_urls", "images", "image_urls", "gallery",
    "image_url", "photo_url", "main_image", "thumbnail_url",
    "minimal_blocks", "dates",
)
# All SourceLot business fields, including procedure deadlines, status, prices,
# and legal/operational attributes, are compared via their persisted columns.
SOURCE_BUSINESS_FIELDS = (
    "canonical_lot_id", "processed_lot_id", "source_system", "external_id",
    "source_url", "lot_url", "etp_url", "title", "description", "category",
    "region_code", "region_name", "address", "cadastral_number",
    "start_price", "current_price", "source_status", "platform_name",
    "platform_code", "procedure_number", "notice_number",
    "efresb_message_number", "debtor_name", "organizer_name",
    "auction_manager_name", "bankruptcy_case_number", "deposit_amount",
    "deposit_percent", "deposit_payment_details", "deposit_deadline",
    "application_start_at", "application_deadline", "auction_at",
    "auction_timezone", "published_at", "auction_step_amount",
    "auction_step_percent", "auction_type", "public_offer_schedule",
    "next_interval_price", "next_price_reduction_at",
    "document_completeness", "inspection_procedure", "organizer_contact",
    "is_active", "is_archived", "archive_reason",
)
MONEY_FIELDS = frozenset({
    "start_price", "current_price", "deposit_amount",
    "auction_step_amount", "next_interval_price",
})


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((k, _stable(v)) for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_stable(v) for v in value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return value


def _value(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record[key]
    return getattr(record, key)


@dataclass(frozen=True)
class ChangeDecision:
    changed: bool
    changed_fields: tuple[str, ...]
    def __bool__(self) -> bool:
        return self.changed


def compare_source_values(existing: Any, projected: Mapping[str, Any]) -> ChangeDecision:
    """Fail closed on missing projected fields; never suppress business changes.

    This compares only SourceLot's business projection. Callers must additionally
    compare ProcessedLot-only attributes before skipping processed upserts.
    """
    differences: list[str] = []
    for field in SOURCE_BUSINESS_FIELDS:
        if field not in projected:
            differences.append(field + ":missing_projection")
            continue
        before, after = _value(existing, field), projected[field]
        if field in MONEY_FIELDS:
            before = Decimal(str(before)) if before is not None else None
            after = Decimal(str(after)) if after is not None else None
        if _stable(before) != _stable(after):
            differences.append(field)
    old_raw = _value(existing, "raw_data") or {}
    new_raw = projected.get("raw_data")
    if not isinstance(old_raw, dict) or not isinstance(new_raw, dict):
        differences.append("raw_data:unverified")
    else:
        for key in RAW_BUSINESS_FIELDS:
            if _stable(old_raw.get(key)) != _stable(new_raw.get(key)):
                differences.append("raw_data." + key)
    return ChangeDecision(bool(differences), tuple(differences))


def detail_is_fresh(
    *,
    fetched_at: datetime | None,
    now: datetime,
    max_age: timedelta,
    detail_version: str | None,
    accepted_versions: frozenset[str],
    previous_listing_fingerprint: str | None = None,
    listing_fingerprint: str | None = None,
    source_updated_at: datetime | None = None,
) -> bool:
    """Fresh detail requires known age and compatible evidence.

    Incompatible/unknown versions, missing fetch timestamps, and a changed
    listing always fail closed. A trusted source update newer than the fetch
    also forces a refresh. Callers still independently compare live catalogue
    price/status/deadlines, even if detail may be reused.
    """
    if max_age <= timedelta(0) or not fetched_at or not accepted_versions:
        return False
    if detail_version not in accepted_versions:
        return False
    if not previous_listing_fingerprint or not listing_fingerprint:
        return False
    if previous_listing_fingerprint != listing_fingerprint:
        return False
    def aware(dt: datetime) -> datetime:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    fetched, observed = aware(fetched_at), aware(now)
    if fetched > observed or observed - fetched > max_age:
        return False
    if source_updated_at and aware(source_updated_at) > fetched:
        return False
    return True
