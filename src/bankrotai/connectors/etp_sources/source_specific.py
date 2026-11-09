from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from bankrotai.domain import NormalizedLot


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

    listing_start = _checked_amount(lot.start_price)
    detail_start = _checked_amount(detail.start_price)
    _checked_amount(lot.current_price)
    _checked_amount(detail.current_price)
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
    })
    lot.raw_data = raw
    return lot
