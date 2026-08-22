from __future__ import annotations

import asyncio

from bankrotai.connectors.registry.torgi_gov import TorgiGovConnector
from bankrotai.scraper_contracts import TorgiGovSearchFilters


def test_gis_connector_paginates_root_categories_independently() -> None:
    connector = TorgiGovConnector()
    calls: list[tuple[str | None, int]] = []

    def fake_search(filters):
        calls.append((filters.category_code, filters.page))
        total_pages = 2 if filters.category_code == "903" else 1
        return [], {"has_more": filters.page < total_pages, "total_pages": total_pages}

    for client in connector._batch_clients:
        client.search_lots = fake_search
    filters = TorgiGovSearchFilters(category_code="903,7,2", page=1)

    async def exercise() -> list[dict]:
        metadata = []
        cursor = None
        while True:
            page = await connector.search(filters, cursor)
            metadata.append(page.metadata)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return metadata

    metadata = asyncio.run(exercise())

    assert sorted(calls) == sorted([
        ("903", 1),
        ("903", 2),
        ("8", 1),
        ("9", 1),
        ("10", 1),
        ("11", 1),
        ("12", 1),
        ("2", 1),
    ])
    assert sum(item["pages_fetched"] for item in metadata) == 8
    assert {
        group: sum(item["category_pages"].get(group, 0) for item in metadata)
        for group in ("903", "7", "2")
    } == {"903": 2, "7": 5, "2": 1}


def test_gis_connector_keeps_legacy_single_category_cursor() -> None:
    connector = TorgiGovConnector()
    calls = []

    def fake_search(filters):
        calls.append((filters.category_code, filters.page))
        return [], {"has_more": filters.page == 1}

    connector.client.search_lots = fake_search
    filters = TorgiGovSearchFilters(category_code="2", page=1)

    first = asyncio.run(connector.search(filters))
    second = asyncio.run(connector.search(filters, first.next_cursor))

    assert first.next_cursor == "2"
    assert second.next_cursor is None
    assert calls == [("2", 1), ("2", 2)]
