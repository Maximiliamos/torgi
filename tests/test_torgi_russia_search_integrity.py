"""Fail closed when a Torgi Russia public search page no longer proves complete data."""

import json

import pytest

from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
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


def test_html_interstitial_is_not_accepted_as_empty_source_page():
    client = TorgiRussiaClient(session=FakeSession("<html><h1>Проверка браузера</h1></html>"))
    with pytest.raises(RuntimeError, match="SSR schema is missing or invalid"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_reported_lots_must_not_be_silently_lost():
    client = TorgiRussiaClient(session=FakeSession(html_with_meta([], total=42)))
    with pytest.raises(RuntimeError, match="positive total but parsed zero"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_bad_lot_records_fail_closed_not_partial_reconciliation():
    records = [{"id": 42, "title": "", "region_title": "Республика Адыгея", "start_price": 12}]
    client = TorgiRussiaClient(session=FakeSession(html_with_meta(records, total=1)))
    with pytest.raises(RuntimeError, match="dropped malformed lot records"):
        client.search_lots(TorgiRussiaSearchFilters(region_id=1))


def test_legitimate_zero_region_stays_zero_with_proven_ssr_meta():
    client = TorgiRussiaClient(session=FakeSession(html_with_meta([], total=0)))
    lots, metadata = client.search_lots(TorgiRussiaSearchFilters(region_id=1))
    assert lots == []
    assert metadata["loaded"] == 0
    assert metadata["total"] == 0
