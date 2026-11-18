"""Auction dates belong to an exact publication, not to its shared asset."""
from datetime import datetime, timezone
import re

from bankrotai.business_time import MOSCOW, to_moscow_datetime
from bankrotai.services.source_verification_metadata import verification_value

AUCTION_DATE_FIELDS = ("application_start_at", "application_deadline", "auction_at")


def source_price_checked_at(source, field, displayed_value):
    """A date/status refresh is not a new independent price observation."""
    if source is None or field not in {"start_price", "current_price"} or displayed_value is None:
        return None
    raw = source.raw_data if isinstance(source.raw_data, dict) else {}
    proof = (raw.get("verified_source_fields") or {}).get(field) or {}
    if (not re.fullmatch(r"[a-f0-9]{64}", str(proof.get("sha256", "")))
            or "value" not in proof or proof["value"] != verification_value(getattr(source, field))
            or proof["value"] != verification_value(displayed_value)):
        return None
    try:
        checked = datetime.fromisoformat(proof.get("checked_at_utc", ""))
    except (TypeError, ValueError):
        return None
    return checked.astimezone(timezone.utc) if checked.tzinfo is not None else None


def _scoped_identity(source, raw, minimum_version):
    version = raw.get("detail_enrichment_version")
    return (isinstance(version, int) and version >= minimum_version
            and raw.get("detail_identity_verified") is True
            and raw.get("detail_external_id") == source.external_id)


def trusted_preview_raw(source) -> dict:
    raw = source.raw_data if isinstance(source.raw_data, dict) else {}
    if source.source_system == "torgi-russia.ru" and not _scoped_identity(source, raw, 7):
        # Older enrichment globally scanned recommendations for preview links
        # and images. Keep that history, but do not show it as this lot's media.
        return {}
    return raw


def source_auction_date(source, field: str) -> datetime | None:
    if source is None:
        return None
    value = getattr(source, field, None)
    if source.source_system in {"lot-online.ru", "torgi-russia.ru"} and value is not None:
        raw = source.raw_data if isinstance(source.raw_data, dict) else {}
        proofs = raw.get("verified_source_fields")
        proof = proofs.get(field) if isinstance(proofs, dict) else {}
        proven = bool(isinstance(proof, dict) and re.fullmatch(r"[a-f0-9]{64}", str(proof.get("sha256", "")))
                      and proof.get("checked_at_utc"))
        if source.source_system == "lot-online.ru":
            scoped = _scoped_identity(source, raw, 4) and field in (raw.get("detail_observed_fields") or [])
        else:
            scoped = _scoped_identity(source, raw, 7) and raw.get(field) not in (None, "")
        if not proven and not scoped:
            # Legacy enrichment could read recommendation timelines. Keep
            # historical values in storage, but do not present them as facts.
            return None
    return to_moscow_datetime(value)


def unverified_auction_fields(source) -> list[str]:
    return [field for field in AUCTION_DATE_FIELDS
            if source is not None and getattr(source, field, None) is not None
            and source_auction_date(source, field) is None]


def business_datetime_iso(value: datetime | None) -> str | None:
    local = to_moscow_datetime(value)
    return local.replace(tzinfo=MOSCOW).isoformat() if local is not None else None
