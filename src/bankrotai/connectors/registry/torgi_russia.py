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
        self._region_ids: list[int] | None = None

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[int, int]:
        if not cursor:
            return 0, 1
        match cursor.split(":"):
            case ["region", region_index, page]:
                return max(0, int(region_index)), max(1, int(page))
            case _:
                # Retain compatibility with an interrupted run using the old numeric cursor.
                return 0, max(1, int(cursor))

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, TorgiRussiaSearchFilters) else TorgiRussiaSearchFilters(**filters)
        if self._region_ids is None:
            self._region_ids = await asyncio.to_thread(self.client.list_region_ids)
        region_index, page = self._decode_cursor(cursor)
        if region_index >= len(self._region_ids):
            return ConnectorPage(items=[], next_cursor=None, metadata={"regions_complete": len(self._region_ids)})
        normalized = replace(normalized, page=page, region_id=self._region_ids[region_index])
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        if metadata.get("has_more"):
            next_cursor = f"region:{region_index}:{normalized.page + 1}"
        elif region_index + 1 < len(self._region_ids):
            next_cursor = f"region:{region_index + 1}:1"
        else:
            next_cursor = None
        metadata.update(
            {
                "region_index": region_index,
                "regions_total": len(self._region_ids),
                "region_id": normalized.region_id,
                "current_category": f"Регион {region_index + 1} из {len(self._region_ids)}",
                "progress_current": region_index + 1,
                "progress_total": len(self._region_ids),
            }
        )
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
