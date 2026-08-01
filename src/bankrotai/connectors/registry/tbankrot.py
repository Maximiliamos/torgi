from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TBankrotSearchFilters


class TBankrotConnector(AuctionConnector):
    source_id = "tbankrot.ru"
    capabilities = frozenset({"search"})

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
