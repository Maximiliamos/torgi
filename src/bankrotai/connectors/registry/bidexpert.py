from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.bidexpert import BidExpertClient
from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import BidExpertSearchFilters


class BidExpertConnector(AuctionConnector):
    source_id = "bidexpert.ru"
    capabilities = frozenset({"search"})

    def __init__(self) -> None:
        self.client = BidExpertClient()

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, BidExpertSearchFilters) else BidExpertSearchFilters(**filters)
        if cursor:
            normalized = replace(normalized, page=max(1, int(cursor)))
        lots, meta = await asyncio.to_thread(self.client.search_lots, normalized)
        return ConnectorPage(lots, str(normalized.page + 1) if meta["has_more"] else None, meta)
