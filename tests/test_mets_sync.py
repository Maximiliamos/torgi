from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bankrotai.domain import NormalizedLot
from bankrotai.connectors.registry.mets_public import MetsPublicPilot
from bankrotai.connectors.registry.mets_sync import (
    MetsSourceCrawler, MetsSyncFilters,
)
from bankrotai.services.etp_mets_full_catalogue import MetsCatalogueScanError


def _lot(external_id: str, price: float, status: str) -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id,
        source="m-ets.ru",
        source_system="m-ets.ru",
        title="Земельный участок",
        description="Земельный участок 01:01:0000001:1",
        category="land",
        region_slug=None,
        region_name=None,
        address=None,
        cadastral_number="01:01:0000001:1",
        vin=None,
        area=None,
        start_price=price,
        current_price=None,
        auction_status=status,
        lot_url=f"https://m-ets.ru/{external_id}",
        source_url=f"https://m-ets.ru/{external_id}",
        detail_level="public_detail",
        raw_data={},
    )


def test_full_mets_crawler_reaches_reported_terminal_without_archival(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod

    pages = {
        1: {
            "total": 3,
            "observations": [
                ("244381-1", "100", "Объявленные торги", "Участок 1"),
                ("244360-1", "200", "Торги в стадии приема заявок", "Участок 2"),
            ],
            "next_page": 2,
        },
        2: {
            "total": 3,
            "observations": [
                ("215028-291", "300", "Объявленные торги", "Участок 3"),
            ],
            "next_page": None,
        },
    }
    monkeypatch.setattr(
        mod, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text=str(page)),
    )
    monkeypatch.setattr(mod, "_parse_page", lambda text, page: pages[page])
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    prices = {"244381-1": 100, "244360-1": 200, "215028-291": 300}
    statuses = {"244381-1": "scheduled", "244360-1": "active", "215028-291": "scheduled"}
    monkeypatch.setattr(
        mod, "parse_mets_lot",
        lambda html, url: _lot(
            url.rsplit("/", 1)[-1], prices[url.rsplit("/", 1)[-1]],
            statuses[url.rsplit("/", 1)[-1]],
        ),
    )
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(pilot, "_get", lambda url: url)
    crawler = MetsSourceCrawler(pilot)

    first = asyncio.run(crawler.search(MetsSyncFilters(detail_delay_seconds=0.5)))
    second = asyncio.run(crawler.search(MetsSyncFilters(detail_delay_seconds=0.5), first.next_cursor))

    assert len(first.items) == 2
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert second.metadata["complete_selected_filter_inventory"] is True
    assert second.metadata["safe_for_archival"] is False
    assert second.metadata["reconcile_missing"] is False
    assert all(
        lot.raw_data["source_scope"] == "full_selected_catalogue_nonreconciling"
        for lot in first.items + second.items
    )


def test_full_mets_crawler_allows_bounded_live_total_change(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod

    monkeypatch.setattr(
        mod, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text=str(page)),
    )
    monkeypatch.setattr(
        mod, "_parse_page",
        lambda text, page: (
            {
                "total": 3,
                "observations": [
                    ("244381-1", "100", "Объявленные торги", "Участок"),
                    ("244382-1", "100", "Объявленные торги", "Участок"),
                ],
                "next_page": 2,
            }
            if page == 1 else
            {
                "total": 4,
                "observations": [
                    ("244382-1", "100", "Объявленные торги", "Участок"),
                    ("244383-1", "100", "Объявленные торги", "Участок"),
                ],
                "next_page": None,
            }
        ),
    )
    monkeypatch.setattr(mod, "parse_mets_lot", lambda html, url: _lot(url.rsplit("/", 1)[-1], 100, "scheduled"))
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(pilot, "_get", lambda url: url)
    crawler = MetsSourceCrawler(pilot)
    first = asyncio.run(crawler.search(MetsSyncFilters()))

    second = asyncio.run(crawler.search(MetsSyncFilters(), first.next_cursor))

    assert [lot.external_id for lot in second.items] == ["244383-1"]
    assert second.next_cursor is None
    assert second.metadata["cross_page_overlap"] == 1
    assert second.metadata["live_total_minimum"] == 3
    assert second.metadata["live_total_maximum"] == 4


def test_full_mets_crawler_rejects_repeated_complete_page(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod

    observations = [
        ("244381-1", "100", "Объявленные торги", "Участок 1"),
        ("244382-1", "100", "Объявленные торги", "Участок 2"),
    ]
    monkeypatch.setattr(
        mod, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text=str(page)),
    )
    monkeypatch.setattr(
        mod, "_parse_page",
        lambda text, page: {
            "total": 4,
            "observations": observations,
            "next_page": page + 1,
        },
    )
    monkeypatch.setattr(mod, "parse_mets_lot", lambda html, url: _lot(url.rsplit("/", 1)[-1], 100, "scheduled"))
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(pilot, "_get", lambda url: url)
    crawler = MetsSourceCrawler(pilot)
    first = asyncio.run(crawler.search(MetsSyncFilters()))

    with pytest.raises(MetsCatalogueScanError, match="repeated a complete page"):
        asyncio.run(crawler.search(MetsSyncFilters(), first.next_cursor))
