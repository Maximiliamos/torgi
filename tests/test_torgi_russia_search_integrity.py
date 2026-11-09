"""Fail closed when a Torgi Russia public search page no longer proves complete data."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
from bankrotai.connectors.registry.torgi_russia import TorgiRussiaConnector
from bankrotai.torgi_russia import TorgiRussiaClient


def html_with_meta(records, *, total):
    flight = "25:" + json.dumps(
        ["$", "$L86", None, {"initialLots": records, "initialMeta": {
            "current_page": 1, "last_page": 1, "total": total, "per_page": 24
        }}],
        ensure_ascii=False, separators=(",", ":"),
    )
    script = json.dumps([1, flight], ensure_ascii=False)
    return f"<html><main></main><script>self.__next_f.push({script})</script></html>"


class FakeResponse:
    status_code = 200
    url = "https://xn----etbpba5admdlad.xn--p1ai/search?page=1"

    def __init__(self, html):
        self.text = html
        self.content = html.encode("utf-8")

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, html):
        self.headers = {}
        self.html = html

    def get(self, *args, **kwargs):
        return FakeResponse(self.html)


class RoutedResponse(FakeResponse):
    def __init__(self, html, url):
        super().__init__(html)
        self.url = url


class RoutedSession:
    def __init__(self, routes):
        self.headers = {}
        self.routes = routes
        self.urls = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return RoutedResponse(self.routes[url], url)


def test_html_interstitial_is_not_accepted_as_empty_source_page():
    client = TorgiRussiaClient(session=FakeSession("<html><h1>Проверка браузера</h1></html>"))
    with pytest.raises(RuntimeError, match="SSR schema is missing or invalid"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_reported_lots_must_not_be_silently_lost():
    client = TorgiRussiaClient(session=FakeSession(html_with_meta([], total=42)))
    with pytest.raises(RuntimeError, match="positive total but parsed zero"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_isolated_bad_lot_is_quarantined_without_stopping_page_walk():
    records = [{"id": 42, "title": "", "region_title": "Республика Адыгея", "start_price": 12}]
    client = TorgiRussiaClient(session=FakeSession(html_with_meta(records, total=1)))
    lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=1))
    assert lots == []
    assert metadata["quarantined_records"] == 1
    assert metadata["complete_source_inventory"] is False
    assert metadata["safe_for_archival"] is False


def test_bad_lot_record_reports_safe_rejection_reason():
    records = [{"id": 42, "title": "", "region_title": "Республика Адыгея"}]
    client = TorgiRussiaClient(session=FakeSession(html_with_meta(records, total=1)))

    _lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=1))
    assert metadata["quarantine_reasons"] == {"empty_title": 1}
    assert "unknown_parser_drop" not in metadata["quarantine_reasons"]


def test_empty_title_is_recovered_only_from_matching_official_mets_detail():
    search_record = {
        "id": 7266197,
        "title": "",
        "region_title": "Воронежская область",
        "start_price": 35612.46,
        "current_price": 35612.46,
        "status": {"title": "Идёт приём заявок"},
    }
    detail_record = {
        **search_record,
        "source_links": [
            {"link": "https://m-ets.ru/242937-2", "title": "Лот на ЭТП"},
            {"link": "https://m-ets.ru/242937", "title": "Торги на ЭТП"},
        ],
    }
    detail_html = html_with_meta([detail_record], total=1)
    mets_html = (
        '<html><head><meta itemprop="sku" content="242937-МЭТС-2"></head><body>'
        '<h1>242937-МЭТС</h1><h2 class="lot-title">Земельный участок 600 кв. м</h2>'
        '<div class="lot-regnumber">Идентификационный номер: 242937-МЭТС-2</div>'
        '<div class="lot-cost-item price"><div class="value">35 612,46 ₽</div>'
        '<div class="title">Начальная цена</div></div>'
        '<div class="lot-cost-item status"><div class="lot-status-name">'
        'Торги в стадии приема заявок</div></div>'
        '<span itemprop="description">Земельный участок в Воронежской области</span>'
        '</body></html>'
    )
    search_url = "https://xn----etbpba5admdlad.xn--p1ai/search"
    detail_url = "https://xn----etbpba5admdlad.xn--p1ai/lot/7266197"
    mets_url = "https://m-ets.ru/242937-2"
    mets_public_url = "https://meetings.m-ets.ru/242937-2"
    session = RoutedSession({
        search_url: html_with_meta([search_record], total=1),
        detail_url: detail_html,
        mets_public_url: mets_html,
    })
    client = TorgiRussiaClient(session=session)

    lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=36))

    assert metadata["loaded"] == 1
    assert lots[0].external_id == "torgi-russia:7266197"
    assert lots[0].title == "Земельный участок 600 кв. м"
    assert lots[0].raw_data["title_evidence"] == "official_mets_detail"
    assert lots[0].raw_data["title_evidence_url"] == mets_public_url
    assert lots[0].raw_data["title_evidence_canonical_url"] == mets_url
    assert lots[0].raw_data["title_evidence_external_id"] == "242937-2"
    assert session.urls == [search_url, detail_url, mets_public_url]


def test_empty_title_fallback_rejects_mets_price_mismatch():
    search_record = {
        "id": 7266197,
        "title": "",
        "region_title": "Воронежская область",
        "start_price": 35612.46,
    }
    detail_record = {
        **search_record,
        "source_links": [{"link": "https://m-ets.ru/242937-2"}],
    }
    mets_html = (
        '<html><head><meta itemprop="sku" content="242937-МЭТС-2"></head><body>'
        '<h1>242937-МЭТС</h1><h2 class="lot-title">Чужой объект</h2>'
        '<div>Идентификационный номер: 242937-МЭТС-2</div>'
        '<div class="lot-cost-item price"><div class="value">99 999 ₽</div>'
        '<div class="title">Начальная цена</div></div>'
        '</body></html>'
    )
    search_url = "https://xn----etbpba5admdlad.xn--p1ai/search"
    detail_url = "https://xn----etbpba5admdlad.xn--p1ai/lot/7266197"
    mets_url = "https://m-ets.ru/242937-2"
    mets_public_url = "https://meetings.m-ets.ru/242937-2"
    client = TorgiRussiaClient(session=RoutedSession({
        search_url: html_with_meta([search_record], total=1),
        detail_url: html_with_meta([detail_record], total=1),
        mets_public_url: mets_html,
    }))

    lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=36))
    assert lots == []
    assert metadata["quarantine_reasons"] == {"empty_title": 1}
    assert metadata["quarantine_samples"][0]["external_id"] == "7266197"
    assert metadata["quarantine_samples"][0]["recovery_failure"] == "RuntimeError:mets_price_mismatch"
    assert "unknown_parser_drop" not in metadata["quarantine_reasons"]


def test_systemic_malformed_page_still_fails_closed():
    records = [
        {"id": item_id, "title": "", "region_title": "Республика Адыгея"}
        for item_id in range(1, 5)
    ]
    client = TorgiRussiaClient(session=FakeSession(html_with_meta(records, total=4)))

    with pytest.raises(RuntimeError, match="systemic malformed records"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_legitimate_zero_region_stays_zero_with_proven_ssr_meta():
    client = TorgiRussiaClient(session=FakeSession(html_with_meta([], total=0)))
    lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=1))
    assert lots == []
    assert metadata["loaded"] == 0
    assert metadata["total"] == 0


def test_repeated_nonempty_page_aborts_source_before_reconciliation():
    """A stale upstream page must not be mistaken for a completed region."""
    connector = TorgiRussiaConnector()
    connector._region_ids = [1]

    class RepeatingClient:
        def search_lots(self, filters):
            return (
                [SimpleNamespace(external_id="torgi-russia:123")],
                {"has_more": filters.page == 1},
            )

    connector.client = RepeatingClient()
    filters = TorgiRussiaSearchFilters(category_id="6", history_only=False, page=1)
    first = asyncio.run(connector.search(filters))
    assert first.next_cursor == "region:0:2"
    with pytest.raises(RuntimeError, match="refusing incomplete full reconciliation"):
        asyncio.run(connector.search(filters, first.next_cursor))


def test_different_pages_are_not_mistaken_for_repeated_inventory():
    connector = TorgiRussiaConnector()
    connector._region_ids = [1]

    class PagedClient:
        def search_lots(self, filters):
            return (
                [SimpleNamespace(external_id=f"torgi-russia:{filters.page}")],
                {"has_more": filters.page == 1},
            )

    connector.client = PagedClient()
    filters = TorgiRussiaSearchFilters(category_id="6", history_only=False, page=1)
    first = asyncio.run(connector.search(filters))
    second = asyncio.run(connector.search(filters, first.next_cursor))
    assert second.next_cursor is None
