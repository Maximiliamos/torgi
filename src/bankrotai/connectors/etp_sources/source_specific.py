from __future__ import annotations

import re

from bankrotai.domain import NormalizedLot


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


def apply_detail(lot: NormalizedLot, detail: NormalizedLot, *, version: int) -> NormalizedLot:
    """Upgrade a shallow provider listing with its verified detail record."""
    lot.title = detail.title or lot.title
    lot.description = detail.description or lot.description
    lot.category = detail.category or lot.category
    lot.address = detail.address or lot.address
    lot.cadastral_number = detail.cadastral_number or lot.cadastral_number
    lot.vin = detail.vin or lot.vin
    lot.area = detail.area or lot.area
    lot.start_price = detail.start_price or lot.start_price
    lot.current_price = detail.current_price or lot.current_price
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
    })
    lot.raw_data = raw
    return lot
