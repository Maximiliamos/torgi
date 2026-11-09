from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
import re

from bankrotai.domain import NormalizedLot


def sistematorg_lot_count(value: str) -> int | None:
    """A Sistematorg listing row represents a trade, potentially containing many lots."""
    match = re.search(r"всего\s+лотов\s*[:：]\s*(\d+)", value, re.IGNORECASE)
    if match is None:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 100_000 else None


def datetime_after(value: str, label: str) -> datetime | None:
    match = re.search(
        label + r"[^\d]{0,40}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def ruson_initial_money(value: str) -> float | None:
    """RUSSIA OnLine renders a labelled initial price that may omit a RUB suffix."""
    match = re.search(
        r"начальн\w*\s+цен\w*\s*[:\-]?\s*(\d[\d\s\u00a0]{0,20}(?:[.,]\d{1,2})?)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(1)).replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def profit_initial_money(value: str) -> float | None:
    """Profit renders a labelled decimal without a RUB suffix."""
    match = re.search(
        r"начальная\s+цена\s+продажи\s+(\d[\d\s\u00a0]{0,20})[.,]\s*(\d{2})(?!\d)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(re.sub(r"[\s\u00a0]", "", match.group(1)) + "." + match.group(2))
    return amount if 0 < amount < 1e15 else None


def _checked_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("etp_invalid_boolean_money")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("etp_invalid_money") from exc
    if not amount.is_finite() or not (Decimal(0) < amount < Decimal("1e15")):
        raise ValueError("etp_invalid_money")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("etp_invalid_money_precision")
    return amount


def apply_detail(lot: NormalizedLot, detail: NormalizedLot, *, version: int) -> NormalizedLot:
    """Upgrade a shallow provider listing only with an isolated verified detail."""
    if detail.external_id != lot.external_id or detail.source_system != lot.source_system:
        raise ValueError("etp_detail_identity_or_source_mismatch")
    if getattr(detail, "detail_level", None) != "detail":
        raise ValueError("etp_detail_not_verified")

    if lot.source_system == "arbbitlot.ru":
        listing_raw = lot.raw_data or {}
        detail_raw = detail.raw_data or {}
        for key in ("procedure_id", "lot_number"):
            listing_identity = listing_raw.get(key)
            detail_identity = detail_raw.get(key)
            if listing_identity is None:
                raise ValueError(f"arbbitlot_catalogue_{key}_unverified")
            if detail_identity is None:
                raise ValueError(f"arbbitlot_detail_{key}_unverified")
            if str(listing_identity) != str(detail_identity):
                raise ValueError(f"arbbitlot_catalogue_detail_{key}_mismatch")

    listing_start = _checked_amount(lot.start_price)
    detail_start = _checked_amount(detail.start_price)
    _checked_amount(lot.current_price)
    _checked_amount(detail.current_price)
    if lot.source_system == "arbbitlot.ru" and detail_start is None:
        raise ValueError("arbbitlot_detail_start_price_unverified")
    if (
        listing_start is not None
        and detail_start is not None
        and abs(listing_start - detail_start) > Decimal("0.01")
    ):
        raise ValueError("etp_detail_start_price_conflicts_with_listing")

    lot.title = detail.title or lot.title
    lot.description = detail.description or lot.description
    lot.category = detail.category or lot.category
    lot.address = detail.address or lot.address
    lot.cadastral_number = detail.cadastral_number or lot.cadastral_number
    lot.vin = detail.vin or lot.vin
    lot.area = detail.area or lot.area
    lot.start_price = detail.start_price if detail.start_price is not None else lot.start_price
    lot.current_price = (
        detail.current_price if detail.current_price is not None else lot.current_price
    )
    lot.auction_status = (
        detail.auction_status if detail.auction_status != "unknown" else lot.auction_status
    )
    lot.application_deadline = detail.application_deadline or lot.application_deadline
    lot.auction_at = detail.auction_at or lot.auction_at
    lot.procedure_number = detail.procedure_number or lot.procedure_number
    if lot.source_system == "arbbitlot.ru":
        lot.deposit_amount = detail.deposit_amount
        lot.deposit_percent = detail.deposit_percent
        lot.auction_step_amount = detail.auction_step_amount
        lot.auction_step_percent = detail.auction_step_percent
        lot.property_type = detail.property_type or lot.property_type
    lot.detail_level = "detail"
    raw = dict(lot.raw_data or {})
    raw.update({
        "detail_enrichment_status": "success",
        "detail_enrichment_version": version,
        "detail_identity_verified": True,
        "detail_start_price_verified": detail.start_price is not None,
        "detail_current_price_verified": detail.current_price is not None,
        "detail_status_verified": detail.auction_status != "unknown",
        "listing_price_used_as_fallback": (
            detail.start_price is None and lot.start_price is not None
        ),
        **({
            "listing_current_price_used_as_fallback": (
                detail.current_price is None and lot.current_price is not None
            ),
        } if lot.source_system == "arbbitlot.ru" else {}),
    })
    lot.raw_data = raw
    return lot
