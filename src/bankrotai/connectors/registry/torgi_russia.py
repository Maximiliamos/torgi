from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import TorgiRussiaClient


class TorgiRussiaConnector(AuctionConnector):
    source_id = "torgi-russia.ru"
    capabilities = frozenset({"search", "detail_enrichment"})

    def __init__(self) -> None:
        self.client = TorgiRussiaClient()

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, TorgiRussiaSearchFilters) else TorgiRussiaSearchFilters(**filters)
        if cursor:
            normalized = replace(normalized, page=max(1, int(cursor)))
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        next_cursor = str(normalized.page + 1) if metadata.get("has_more") else None
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)

    async def enrich_lot(self, lot):
        page_url = lot.source_url or lot.lot_url or ""
        response = await asyncio.to_thread(self.client.session.get, page_url, timeout=self.client.timeout)
        response.raise_for_status()
        detail = self.client.parse_lot_page(response.text, response.url or page_url)
        raw = dict(lot.raw_data or {})
        raw.update(detail.as_dict())
        raw["detail_enrichment_status"] = "success"
        lot.raw_data = raw
        lot.address = detail.address or lot.address
        lot.application_start_at = detail.application_start_at or lot.application_start_at
        lot.application_deadline = detail.application_deadline or lot.application_deadline
        lot.auction_at = detail.auction_at or lot.auction_at
        lot.auction_timezone = lot.auction_timezone or "Europe/Moscow"
        lot.procedure_number = detail.procedure_number or lot.procedure_number
        lot.detail_level = "detail"
        return lot
