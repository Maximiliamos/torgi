from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.scraper_contracts import TBankrotSearchFilters


class TBankrotConnector(AuctionConnector):
    source_id = "tbankrot.ru"
    capabilities = frozenset({"search", "detail_enrichment"})
    detail_enrichment_version = 3

    def __init__(self) -> None:
        from bankrotai.scrapers import TBankrotClient

        self.client = TBankrotClient()

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, TBankrotSearchFilters) else TBankrotSearchFilters(**filters)
        if cursor:
            normalized = replace(normalized, page=max(1, int(cursor)))
        lots, metadata = await asyncio.to_thread(self.client.search_filtered_lots, normalized)
        next_cursor = str(normalized.page + 1) if metadata.get("has_more") else None
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)


    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        """Fill missing source-backed fields without requiring every listing to hit detail."""
        raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
        listing_complete = (
            (lot.start_price is not None or lot.current_price is not None)
            and lot.application_deadline is not None
            and lot.auction_at is not None
            and lot.auction_status not in {"", "unknown"}
        )
        if listing_complete:
            raw.update({
                "detail_enrichment_status": "success",
                "detail_enrichment_version": self.detail_enrichment_version,
                "detail_enrichment_skipped": "listing_complete",
            })
            lot.raw_data = raw
            return lot
        if not lot.lot_url:
            raw["detail_enrichment_status"] = "failed"
            lot.raw_data = raw
            return lot

        fields = await asyncio.to_thread(self.client.fetch_detail_fields, lot.lot_url)
        if fields.get("start_price") is not None:
            lot.start_price = fields["start_price"]
        if fields.get("current_price") is not None:
            lot.current_price = fields["current_price"]
        if fields.get("application_deadline") is not None:
            lot.application_deadline = fields["application_deadline"]
        if fields.get("auction_at") is not None:
            lot.auction_at = fields["auction_at"]
        if fields.get("auction_status") not in {None, "", "unknown"}:
            lot.auction_status = str(fields["auction_status"])
        if lot.application_deadline or lot.auction_at:
            lot.auction_timezone = "Europe/Moscow"
        images = fields.get("image_urls") or []
        if images:
            raw["image_urls"] = list(dict.fromkeys([*raw.get("image_urls", []), *images]))
        raw.update({
            "detail_enrichment_status": "success",
            "detail_enrichment_version": self.detail_enrichment_version,
            "detail_source_url": fields.get("detail_url"),
            "detail_start_price_verified": fields.get("start_price") is not None,
            "detail_current_price_verified": fields.get("current_price") is not None,
            "detail_status_verified": fields.get("auction_status") not in {None, "", "unknown"},
            "detail_dates_verified": bool(
                fields.get("application_deadline") or fields.get("auction_at")
            ),
        })
        lot.raw_data = raw
        lot.detail_level = "detail"
        return lot
