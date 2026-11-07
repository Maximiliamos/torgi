"""Read-only NISTP per-lot detail adapter for a shared procedure page.

A single trade URL can contain many different assets. The source-provided
numbered table and the requested (trade_nid, lot_number) must both match.
The network call refuses redirects; no uploads, writes or archival.
"""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import TYPE_CHECKING

from bankrotai.connectors.etp_sources.nistp_detail_sections import get_nistp_lot
from bankrotai.domain import NormalizedLot

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import HtmlEtpConnector


async def fetch_nistp_lot(connector: HtmlEtpConnector, external_id: str) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import (
        SourceSchemaError, _category, _safe_provider_url, _status,
    )

    match = re.fullmatch(r"nistp\.ru:([1-9]\d{0,11}):([1-9]\d{0,4})", external_id)
    if not match:
        raise SourceSchemaError("nistp.ru: invalid per-lot external_id")
    trade_nid, lot_text = match.groups()
    detail_url = _safe_provider_url(
        connector.profile, f"https://nistp.ru/bankrot/trade_view.php?trade_nid={trade_nid}"
    )
    response = await asyncio.to_thread(
        connector._read_request, "get", detail_url,
        timeout=connector.timeout, allow_redirects=False,
    )
    section = get_nistp_lot(
        response.content, trade_nid=trade_nid, lot_number=int(lot_text),
    )
    if section.external_id != external_id:
        raise SourceSchemaError("nistp.ru: fetched individual lot identity mismatch")
    return NormalizedLot(
        external_id=section.external_id,
        source="nistp",
        source_system="nistp.ru",
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
        auction_status=_status(section.status_text),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "trade_id": section.trade_code,
            "lot_number": str(section.lot_number),
            "native_lot_nid": section.native_lot_nid,
            "individual_lot_identity_verified": True,
            "detail_start_price_verified": True,
            "detail_status_normalized": _status(section.status_text),
            "detail_deposit_verified": section.deposit_rub is not None,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name=connector.profile.platform_name,
        platform_code=connector.profile.platform_code,
        procedure_number=section.trade_code,
        deposit_amount=float(section.deposit_rub)
        if section.deposit_rub is not None else None,
    )


async def enrich_nistp_lot(
    connector: HtmlEtpConnector, listing: NormalizedLot,
) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import SourceSchemaError
    from bankrotai.connectors.etp_sources.source_specific import apply_detail

    if listing.source_system != "nistp.ru":
        raise SourceSchemaError("nistp.ru: cross-source listing cannot be enriched")
    detail = await fetch_nistp_lot(connector, listing.external_id)
    if detail.external_id != listing.external_id:
        raise SourceSchemaError("nistp.ru: detail identity mismatch")
    listed_trade = str((listing.raw_data or {}).get("trade_id") or "")
    if listed_trade and listed_trade != detail.procedure_number:
        raise SourceSchemaError("nistp.ru: procedure code mismatch")
    if listing.start_price is not None and detail.start_price is not None:
        # Initial price is an invariant (unlike current bidding price).
        a = Decimal(str(listing.start_price))
        b = Decimal(str(detail.start_price))
        if not a.is_finite() or abs(a - b) > Decimal("0.01"):
            raise SourceSchemaError("nistp.ru: listing and individual detail price differ")
    if (listing.cadastral_number and detail.cadastral_number
            and listing.cadastral_number != detail.cadastral_number):
        raise SourceSchemaError("nistp.ru: listing and detail cadastre differ")
    enriched = apply_detail(listing, detail, version=connector.detail_enrichment_version)
    enriched.deposit_amount = detail.deposit_amount
    raw = dict(enriched.raw_data or {})
    raw.update(detail.raw_data)
    raw["detail_enrichment_status"] = "success"
    raw["detail_enrichment_version"] = connector.detail_enrichment_version
    enriched.raw_data = raw
    return enriched
