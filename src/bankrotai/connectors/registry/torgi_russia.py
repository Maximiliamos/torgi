from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import TorgiRussiaClient


class TorgiRussiaConnector(AuctionConnector):
    source_id = "torgi-russia.ru"
    detail_enrichment_version = 4
    compatible_detail_enrichment_versions = frozenset({3, 4})
    capabilities = frozenset({"search", "detail_enrichment"})

    def __init__(self) -> None:
        self.client = TorgiRussiaClient()
        self._previous_page_ids: frozenset[str] | None = None

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 1
        if cursor.startswith("region:"):
            # Interrupted runs from the retired region-API strategy must restart
            # on the public catalog instead of resuming inside an obsolete region.
            return 1
        return max(1, int(cursor))

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = (
            filters
            if isinstance(filters, TorgiRussiaSearchFilters)
            else TorgiRussiaSearchFilters(**filters)
        )
        page = self._decode_cursor(cursor)
        normalized = replace(normalized, page=page, region_id=None)
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        page_ids = frozenset(lot.external_id for lot in lots)
        repeated_page = bool(page_ids) and page_ids == self._previous_page_ids
        if repeated_page:
            metadata["has_more"] = False
            metadata["repeated_page_guard"] = True
        self._previous_page_ids = page_ids
        next_cursor = str(page + 1) if metadata.get("has_more") else None
        metadata.update(
            {
                "current_category": "Недвижимость",
                "progress_current": page,
                "progress_total": metadata.get("total_pages"),
            }
        )
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)

    async def enrich_lot(self, lot):
        html, page_url = await asyncio.to_thread(self.client.fetch_lot_page, lot.external_id)
        detail = self.client.parse_lot_page(html, page_url)
        raw = dict(lot.raw_data or {})
        raw.update(detail.as_dict())
        raw["detail_enrichment_status"] = "success"
        raw["detail_enrichment_version"] = self.detail_enrichment_version
        lot.raw_data = raw
        lot.description = str(detail.description or lot.description)[:5000]
        lot.address = detail.address or lot.address
        lot.cadastral_number = (
            detail.cadastral_numbers[0]
            if detail.cadastral_numbers
            else lot.cadastral_number
        )
        lot.application_deadline = detail.application_deadline or lot.application_deadline
        lot.auction_at = detail.auction_at or lot.auction_at
        lot.procedure_number = detail.procedure_number or lot.procedure_number
        lot.auction_timezone = lot.auction_timezone or "Europe/Moscow"
        lot.detail_level = "detail"
        return lot
