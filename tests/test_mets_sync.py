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
        title="Нежилое помещение",
        description="Нежилое помещение 01:01:0000001:1",
        category="commercial_room",
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


def test_full_mets_rejects_systemic_unverified_selected_catalogue(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod

    identities = [f"244381-{i}" for i in range(1, 6)]
    monkeypatch.setattr(
        mod, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text="catalogue"),
    )
    monkeypatch.setattr(
        mod, "_parse_page",
        lambda html, page: {
            "total": 5,
            "observations": [
                (identity, "100", "Объявленные торги", "Земельный участок")
                for identity in identities
            ],
            "next_page": None,
        },
    )
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)

    def incomplete_detail(_html, url):
        lot = _lot(url.rsplit("/", 1)[-1], 100, "scheduled")
        lot.cadastral_number = None  # no independently verified cadastre
        return lot

    monkeypatch.setattr(mod, "parse_mets_lot", incomplete_detail)
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(pilot, "_get", lambda url: "lot html")
    with pytest.raises(MetsCatalogueScanError, match="systemic detail verification"):
        asyncio.run(MetsSourceCrawler(pilot).search(MetsSyncFilters()))


def test_prepared_mets_page_is_refreshed_before_detail_validation(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod

    requests_by_page: dict[int, int] = {}

    def request_page(_session, page, sleep):
        requests_by_page[page] = requests_by_page.get(page, 0) + 1
        phase = "prepared" if requests_by_page[page] == 1 else "fresh"
        return SimpleNamespace(text=f"{phase}:{page}")

    def parse_page(text, page):
        price = "100" if text.startswith("prepared:") else "110"
        return {
            "total": 1,
            "observations": [
                ("244381-1", price, "Объявленные торги", "Земельный участок")
            ],
            "next_page": None,
        }

    monkeypatch.setattr(mod, "_request_page", request_page)
    monkeypatch.setattr(mod, "_parse_page", parse_page)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        mod,
        "parse_mets_lot",
        lambda html, url: _lot(url.rsplit("/", 1)[-1], 110, "scheduled"),
    )
    pilot = MetsPublicPilot(session=object())
    monkeypatch.setattr(pilot, "_get", lambda url: "lot html")
    crawler = MetsSourceCrawler(pilot)
    filters = MetsSyncFilters()

    prepared = crawler.prepare_catalogue(filters)
    page = asyncio.run(crawler.search(filters))

    assert prepared["pages"] == 1
    assert requests_by_page == {1: 2}
    assert len(page.items) == 1
    assert page.items[0].start_price == 110
    assert page.metadata["prepared_catalogue_page_lots"] == 1
    assert page.metadata["catalogue_identity_drift"] == 0
    assert page.metadata["catalogue_observation_drift"] == 1


def test_full_mets_provider_refusal_is_fatal_even_for_one_card(monkeypatch) -> None:
    import bankrotai.connectors.registry.mets_sync as mod
    from bankrotai.connectors.registry.mets_public import MetsEvidenceError

    monkeypatch.setattr(
        mod, "_request_page",
        lambda session, page, sleep: SimpleNamespace(text="catalogue"),
    )
    monkeypatch.setattr(
        mod, "_parse_page",
        lambda html, page: {
            "total": 1,
            "observations": [
                ("244381-1", "100", "Объявленные торги", "Земельный участок")
            ],
            "next_page": None,
        },
    )
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    pilot = MetsPublicPilot(session=object())

    def blocked(_url):
        raise MetsEvidenceError("M-ETS provider access explicitly refused")

    monkeypatch.setattr(pilot, "_get", blocked)
    with pytest.raises(MetsCatalogueScanError, match="detail access/refusal failure"):
        asyncio.run(MetsSourceCrawler(pilot).search(MetsSyncFilters()))
