from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TorgiGovSearchFilters


class TorgiGovConnector(AuctionConnector):
    source_id = "torgi.gov.ru"
    capabilities = frozenset({"search"})

    def __init__(self) -> None:
        from bankrotai.scrapers import TorgiGovClient

        self.client = TorgiGovClient()

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, TorgiGovSearchFilters) else TorgiGovSearchFilters(**filters)
        categories = self._category_sequence(normalized.category_code)
        category_index, page_number = self._cursor_position(cursor, len(categories), normalized.page)
        requested_group, source_category = categories[category_index]
        normalized = replace(normalized, category_code=source_category, page=page_number)
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        metadata = {
            **metadata,
            "requested_category_group": requested_group,
            "source_category_code": source_category,
            "category_index": category_index,
            "category_count": len(categories),
        }
        if metadata.get("has_more"):
            next_cursor = self._encode_cursor(category_index, page_number + 1, len(categories))
        elif category_index + 1 < len(categories):
            next_cursor = self._encode_cursor(category_index + 1, 1, len(categories))
        else:
            next_cursor = None
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)

    def _category_sequence(self, category_code: str | None) -> list[tuple[str, str]]:
        requested = [item.strip() for item in (category_code or "").split(",") if item.strip()]
        if not requested:
            requested = self.client.REAL_ESTATE_ROOT_CATEGORY_CODES.split(",")
        categories: list[tuple[str, str]] = []
        for group in requested:
            expanded = self.client.CATEGORY_GROUP_CODE_MAP.get(group, group)
            categories.extend((group, child.strip()) for child in expanded.split(",") if child.strip())
        return categories

    @staticmethod
    def _cursor_position(cursor: str | None, category_count: int, initial_page: int) -> tuple[int, int]:
        if not cursor:
            return 0, max(1, initial_page)
        if ":" not in cursor:
            return 0, max(1, int(cursor))
        category_index_text, page_text = cursor.split(":", 1)
        category_index = int(category_index_text)
        if category_index < 0 or category_index >= category_count:
            raise ValueError("invalid GIS category cursor")
        return category_index, max(1, int(page_text))

    @staticmethod
    def _encode_cursor(category_index: int, page_number: int, category_count: int) -> str:
        if category_count == 1:
            return str(page_number)
        return f"{category_index}:{page_number}"
