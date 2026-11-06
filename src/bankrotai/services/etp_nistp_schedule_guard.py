"""Strict provenance gates for scheduled NISTP five-page discovery.

The public filtered source has included 43 terminal-state rows, and shared
trade 502379 has catalogue-only high-numbered lots without individual detail.
Never treat either case as confirmed active property inventory.
"""
from __future__ import annotations

from bankrotai.domain import NormalizedLot

_NISTP_ACTIVE_STATES = frozenset({
    "торги объявлены", "прием заявок", "идут торги",
})
_ACTIVE = frozenset({"active", "scheduled"})


def catalogue_row_eligible_for_detail(lot: NormalizedLot) -> bool:
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    status = " ".join(
        str(raw.get("catalogue_source_status_text") or "").split()
    ).casefold().replace("ё", "е")
    return (
        status in _NISTP_ACTIVE_STATES
        and lot.auction_status in _ACTIVE
        and lot.start_price is not None
        and lot.start_price > 0
    )


def independent_detail_proven(lot: NormalizedLot) -> bool:
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    return (
        lot.detail_level == "detail"
        and raw.get("detail_enrichment_status") == "success"
        and raw.get("individual_lot_identity_verified") is True
        and raw.get("detail_start_price_verified") is True
        and raw.get("detail_status_normalized") in _ACTIVE
        and lot.auction_status in _ACTIVE
        and lot.start_price is not None
        and lot.start_price > 0
    )
