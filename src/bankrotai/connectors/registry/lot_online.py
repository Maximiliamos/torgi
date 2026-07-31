from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import LotOnlineSearchFilters


class LotOnlineConnector(AuctionConnector):
    source_id = "lot-online.ru"
    capabilities = frozenset({"search"})

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
