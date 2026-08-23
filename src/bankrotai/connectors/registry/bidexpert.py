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
        category = normalized.category.lower()
        if category == "all":
            phase, page = (cursor.split(":", 1) if cursor else ("realty", "1"))
            if phase not in {"realty", "land"}:
                raise ValueError(f"Invalid BidExpert cursor phase: {phase}")
            normalized = replace(normalized, category=phase, page=max(1, int(page)))
        elif cursor:
            normalized = replace(normalized, page=max(1, int(cursor)))
        lots, meta = await asyncio.to_thread(self.client.search_lots, normalized)
        if category == "all":
            next_cursor = f"{normalized.category}:{normalized.page + 1}" if meta["has_more"] else (
                "land:1" if normalized.category == "realty" else None
            )
        else:
            next_cursor = str(normalized.page + 1) if meta["has_more"] else None
        meta["category_phase"] = normalized.category
        meta["requested_category_group"] = normalized.category
        return ConnectorPage(lots, next_cursor, meta)
