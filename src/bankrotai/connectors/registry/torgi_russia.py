from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.regions import REGION_DIRECTORY
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.torgi_russia import TorgiRussiaClient


class TorgiRussiaConnector(AuctionConnector):
    source_id = "torgi-russia.ru"
    detail_enrichment_version = 4
    compatible_detail_enrichment_versions = frozenset({3, 4})
    # Listing-page price/status/photo changes do not invalidate stable detail
    # fields such as address/cadastral/procedure data. This also prevents the
    # new Next.js transport shape from forcing a mass detail refetch.
    detail_enrichment_on_listing_change = False
    capabilities = frozenset({"search", "detail_enrichment"})

    def __init__(self) -> None:
        self.client = TorgiRussiaClient()
        # The new public site caps an unsegmented search at 5,000 rows while
        # advertising substantially more active real-estate lots. Region IDs
        # match the canonical subject codes already verified by this connector.
        self._region_ids = [int(region.code) for region in REGION_DIRECTORY]
        self._previous_page_signature: tuple[int, frozenset[str]] | None = None

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[int, int]:
        if not cursor:
            return 0, 1
        if cursor.startswith("region:"):
            match cursor.split(":"):
                case ["region", region_index, page]:
                    return max(0, int(region_index)), max(1, int(page))
                case _:
                    raise ValueError(f"Invalid Torgi Russia cursor: {cursor}")
        # Compatibility with the short-lived unsegmented public-catalog cursor.
        return 0, max(1, int(cursor))

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = (
            filters
            if isinstance(filters, TorgiRussiaSearchFilters)
            else TorgiRussiaSearchFilters(**filters)
        )
        region_index, page = self._decode_cursor(cursor)
        if region_index >= len(self._region_ids):
            return ConnectorPage(
                items=[],
                next_cursor=None,
                metadata={"regions_complete": len(self._region_ids)},
            )

        region_id = self._region_ids[region_index]
        normalized = replace(normalized, page=page, region_id=region_id)
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)

        page_ids = frozenset(lot.external_id for lot in lots)
        signature = (region_id, page_ids)
        repeated_page = (
            bool(page_ids)
            and self._previous_page_signature == signature
        )
        if repeated_page:
            metadata["has_more"] = False
            metadata["repeated_page_guard"] = True
        self._previous_page_signature = signature

        if metadata.get("has_more"):
            next_cursor = f"region:{region_index}:{page + 1}"
        elif region_index + 1 < len(self._region_ids):
            next_cursor = f"region:{region_index + 1}:1"
        else:
            next_cursor = None

        metadata.update(
            {
                "region_index": region_index,
                "regions_total": len(self._region_ids),
                "region_id": region_id,
                "current_category": f"Регион {region_index + 1} из {len(self._region_ids)}",
                "progress_current": region_index + 1,
                "progress_total": len(self._region_ids),
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
