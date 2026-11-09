"""Read-only Vertrades per-lot detail adapter for shared procedure cards."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import re
from typing import TYPE_CHECKING

from bankrotai.connectors.etp_sources.vertrades_detail_sections import get_vertrades_lot
from bankrotai.domain import NormalizedLot

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import HtmlEtpConnector


def _vertrades_status(value: str) -> str:
    """Normalize only statuses proven on the Vertrades per-lot card."""
    from bankrotai.connectors.etp_sources.status_normalization import _status

    normalized = _status(value)
    if normalized != "unknown":
        return normalized
    if re.search(r"торги\s+по\s+лоту\s+отмен[её]н", value, re.IGNORECASE):
        return "closed"
    if re.search(r"заверш[её]нн?ые\s+торги", value, re.IGNORECASE):
        return "closed"
    return "unknown"


async def fetch_vertrades_lot(connector: HtmlEtpConnector, external_id: str) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import (
        SourceSchemaError, _category, _safe_provider_url,
    )
    match = re.fullmatch(r"vertrades\.ru:([1-9]\d{0,11}):([1-9]\d{0,5})", external_id)
    if not match:
        raise SourceSchemaError("vertrades.ru: invalid per-lot external_id")
    provider_id, lot_text = match.groups()
    detail_url = _safe_provider_url(
        connector.profile, f"https://arest.vertrades.ru/showTrades?id={provider_id}"
    )
    response = await asyncio.to_thread(
        connector._read_request, "get", detail_url,
        timeout=connector.timeout, allow_redirects=False,
    )
    try:
        section = get_vertrades_lot(
            response.content, provider_id=provider_id, lot_number=int(lot_text),
        )
    except ValueError as exc:
        raise SourceSchemaError(f"vertrades.ru: {exc}") from exc
    if section.external_id != external_id:
        raise SourceSchemaError("vertrades.ru: fetched individual lot identity mismatch")
    normalized_status = _vertrades_status(section.status_text)
    if normalized_status == "unknown":
        raise SourceSchemaError("vertrades.ru: unrecognized explicit lot status")
    return NormalizedLot(
        external_id=section.external_id,
        source="vertrades",
        source_system="vertrades.ru",
        title=section.title,
        description=section.description,
        category=_category(section.title + " " + section.description),
        region_slug=None,
        region_name=None,
        address=section.address,
        cadastral_number=section.cadastral_number,
        vin=None,
        area=section.area_sqm,
        start_price=float(section.initial_price_rub),
        current_price=None,
        auction_status=normalized_status,
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "provider_procedure_id": section.provider_id,
            "trade_id": section.procedure_number,
            "lot_number": str(section.lot_number),
            "individual_lot_identity_verified": True,
            "detail_start_price_verified": True,
            "detail_status_verified": True,
            "detail_dates_verified": (
                section.application_deadline is not None or section.auction_at is not None
            ),
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name=connector.profile.platform_name,
        platform_code=connector.profile.platform_code,
        procedure_number=section.procedure_number,
        deposit_amount=float(section.deposit_rub) if section.deposit_rub is not None else None,
        application_start_at=section.application_start_at,
        application_deadline=section.application_deadline,
        auction_at=section.auction_at,
    )


async def enrich_vertrades_lot(
    connector: HtmlEtpConnector, listing: NormalizedLot,
) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import SourceSchemaError
    from bankrotai.connectors.etp_sources.source_specific import apply_detail

    if listing.source_system != "vertrades.ru":
        raise SourceSchemaError("vertrades.ru: cross-source listing cannot be enriched")
    detail = await fetch_vertrades_lot(connector, listing.external_id)
    listed_trade = str((listing.raw_data or {}).get("trade_id") or "")
    if listed_trade and listed_trade != detail.procedure_number:
        raise SourceSchemaError("vertrades.ru: procedure code mismatch")
    listed_lot = str((listing.raw_data or {}).get("lot_number") or "")
    if listed_lot and listed_lot != str((detail.raw_data or {}).get("lot_number") or ""):
        raise SourceSchemaError("vertrades.ru: lot number mismatch")
    if listing.cadastral_number and detail.cadastral_number:
        if listing.cadastral_number != detail.cadastral_number:
            raise SourceSchemaError("vertrades.ru: listing and detail cadastre differ")
    if listing.start_price is not None and detail.start_price is not None:
        a = Decimal(str(listing.start_price))
        b = Decimal(str(detail.start_price))
        if not a.is_finite() or not b.is_finite() or abs(a - b) > Decimal("0.01"):
            raise SourceSchemaError("vertrades.ru: listing and individual detail price differ")

    enriched = apply_detail(listing, detail, version=connector.detail_enrichment_version)
    enriched.deposit_amount = detail.deposit_amount
    enriched.application_start_at = detail.application_start_at
    raw = dict(enriched.raw_data or {})
    raw.update(detail.raw_data)
    raw["detail_enrichment_status"] = "success"
    raw["detail_enrichment_version"] = connector.detail_enrichment_version
    enriched.raw_data = raw
    return enriched
