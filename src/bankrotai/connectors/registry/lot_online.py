from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import LotOnlineSearchFilters


class LotOnlineConnector(AuctionConnector):
    source_id = "lot-online.ru"
    capabilities = frozenset({"search", "detail_enrichment"})

    def __init__(self) -> None:
        from bankrotai.scrapers import LotOnlineClient

        self.client = LotOnlineClient()

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, LotOnlineSearchFilters) else LotOnlineSearchFilters(**filters)
        if cursor:
            normalized = replace(normalized, page=max(1, int(cursor)))
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        next_cursor = str(normalized.page + 1) if metadata.get("has_more") else None
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)

    async def enrich_lot(self, lot):
        detail = await asyncio.to_thread(self.client.fetch_detail_fields, lot.source_url or lot.lot_url or "")
        raw = dict(lot.raw_data or {})
        raw.update(detail)
        raw["detail_enrichment_status"] = "success"
        lot.raw_data = raw
        lot.address = detail.get("address") or lot.address
        cadastral_numbers = detail.get("cadastral_numbers") or []
        lot.cadastral_number = (cadastral_numbers[0] if cadastral_numbers else None) or lot.cadastral_number
        lot.description = detail.get("description") or lot.description
        lot.detail_level = "detail"
        return lot
