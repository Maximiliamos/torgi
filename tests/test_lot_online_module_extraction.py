"""Regression coverage for RAD/LotOnline client module extraction (BAT-308)."""

import requests
from unittest.mock import patch

from bankrotai import scrapers
from bankrotai.connectors.lot_online import LotOnlineClient, LotOnlineClientError
from bankrotai.scraper_contracts import LotOnlineSearchFilters


def test_legacy_scrapers_exports_preserve_client_identity() -> None:
    assert scrapers.LotOnlineClient is LotOnlineClient
    assert scrapers.LotOnlineClientError is LotOnlineClientError


def test_public_filter_contract_is_unchanged() -> None:
    original = scrapers.LotOnlineClient()._build_query_params(LotOnlineSearchFilters())
    extracted = LotOnlineClient()._build_query_params(LotOnlineSearchFilters())
    assert extracted == original
    assert LotOnlineClient.RETRY_STATUS_CODES == frozenset({429, 502, 503, 504})
    assert LotOnlineClient.RETRY_DELAYS_SECONDS == (1, 2, 4, 8)


def test_legacy_time_sleep_patch_still_affects_extracted_class() -> None:
    class FakeResponse:
        status_code = 503

        @staticmethod
        def raise_for_status() -> None:
            raise requests.HTTPError("503")

    class FakeSession:
        def __init__(self) -> None:
            self.headers = {}
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    with patch("bankrotai.scrapers.time.sleep") as sleeper:
        try:
            LotOnlineClient(session=session)._get_with_retry(LotOnlineClient.SEARCH_ENDPOINT)
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("Exhausted retry must fail closed, not silently return empty catalogue")
    assert session.calls == 5
    assert sleeper.call_count == 4
