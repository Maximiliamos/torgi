from __future__ import annotations

import asyncio

from bankrotai.connectors.registry.torgi_gov import TorgiGovConnector
from bankrotai.scraper_contracts import TorgiGovSearchFilters


def test_gis_connector_paginates_root_categories_independently() -> None:
    connector = TorgiGovConnector()
    calls: list[tuple[str | None, int]] = []

    def fake_search(filters):
        calls.append((filters.category_code, filters.page))
        has_more = filters.category_code == "903" and filters.page == 1
        return [], {"has_more": has_more}

    connector.client.search_lots = fake_search
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

    assert calls == [
        ("903", 1),
        ("903", 2),
        ("8", 1),
        ("9", 1),
        ("10", 1),
        ("11", 1),
        ("12", 1),
        ("2", 1),
    ]
    assert [item["requested_category_group"] for item in metadata] == [
        "903", "903", "7", "7", "7", "7", "7", "2",
    ]
    assert [item["source_category_code"] for item in metadata] == [
        "903", "903", "8", "9", "10", "11", "12", "2",
    ]


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
