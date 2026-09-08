from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import TorgiRussiaClient


class TorgiRussiaConnector(AuctionConnector):
    source_id = "torgi-russia.ru"
    detail_enrichment_version = 3
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
        detail = self.client.parse_detail_payload(
            await asyncio.to_thread(self.client.fetch_lot_payload, lot.external_id)
        )
        raw = dict(lot.raw_data or {})
        raw.update(detail)
        raw["detail_enrichment_status"] = "success"
        raw["detail_enrichment_version"] = self.detail_enrichment_version
        lot.raw_data = raw
        lot.description = str(detail.get("description") or lot.description)[:5000]
        lot.address = detail.get("address") or lot.address
        cadastres = detail.get("cadastral_numbers") or []
        lot.cadastral_number = cadastres[0] if cadastres else lot.cadastral_number
        lot.auction_timezone = lot.auction_timezone or "Europe/Moscow"
        lot.detail_level = "detail"
        return lot
