"""CDTRF production regression: one malformed lot must not erase a whole page.

All HTTP replies and details are synthetic. No requests or DB writes.
"""
from __future__ import annotations

import asyncio

import pytest
import requests

from bankrotai.connectors.registry.cdtrf_public import CdtrfEvidenceError
from bankrotai.connectors.registry.cdtrf_sync import (
    CdtrfCatalogueScanError, CdtrfSourceCrawler, CdtrfSyncFilters,
)
from bankrotai.domain import NormalizedLot


class _Reply:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    content = b'{"success":true}'

    def json(self):
        return {
            "success": True, "totalCount": 20,
            "items": [{
                "tradeId": 400000 + i,
                "tradeLotId": 500000 + i,
                "categories": ",21,", "actualPrice": 500000,
            } for i in range(1, 21)],
        }


class _Session:
    def get(self, url, *, timeout, allow_redirects, headers):
        assert "PageSize=20" in url and "PageNum=1" in url
        assert not allow_redirects
        return _Reply()


def _lot(trade, lot):
    return NormalizedLot(
        external_id=f"cdtrf:{lot}", source="cdtrf.ru",
        source_system="cdtrf.ru", title="Земельный участок",
        description="Земельный участок, 32:10:0010101:42",
        category="land", region_slug=None, region_name=None,
        address=None, cadastral_number="32:10:0010101:42",
        vin=None, area=None, start_price=500000,
        current_price=None, auction_status="active",
        lot_url=f"https://torgi.cdtrf.ru/trades/{trade}",
        source_url=None, detail_level="public_detail", raw_data={},
    )


def test_isolated_mismatched_and_invalid_detail_are_rejected(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_sync as module

    def read(trade, lot, *, session):
        i = int(lot) - 500000
        if i == 3:
            raise CdtrfEvidenceError("Trade ID or title disagreement between URL, HTML and Nuxt")
        if i == 4:
            raise CdtrfEvidenceError("Price disagreement between HTML and SSR")
        if i == 5:
            raise CdtrfEvidenceError("No CDTRF SSR data")
        return _lot(trade, lot)

    monkeypatch.setattr(module, "fetch_verified_cdtrf_lot", read)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    page = asyncio.run(
        CdtrfSourceCrawler(_Session()).search(
            CdtrfSyncFilters(page_size=20), None
        )
    )
    assert page.next_cursor is None
    assert len(page.items) == 17
    assert page.metadata["details_rejected"] == 3
    assert page.metadata["rejection_codes"]["lot_identity_mismatch"] == 1
    assert page.metadata["rejection_codes"]["price_mismatch"] == 1
    assert page.metadata["rejection_codes"]["ssr_schema_changed"] == 1
    assert page.metadata["safe_for_archival"] is False
    assert page.metadata["reconcile_missing"] is False


def test_systemic_ssr_breakage_remains_fail_closed(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_sync as module

    def broken(*args, **kwargs):
        raise CdtrfEvidenceError("No CDTRF SSR data")

    monkeypatch.setattr(module, "fetch_verified_cdtrf_lot", broken)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(CdtrfCatalogueScanError, match="acceptance collapsed"):
        asyncio.run(
            CdtrfSourceCrawler(_Session()).search(
                CdtrfSyncFilters(page_size=20), None
            )
        )


def test_network_failure_remains_fail_closed(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_sync as module

    def timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("detail timed out")

    monkeypatch.setattr(module, "fetch_verified_cdtrf_lot", timeout)
    with pytest.raises(CdtrfCatalogueScanError, match="network failure"):
        asyncio.run(
            CdtrfSourceCrawler(_Session()).search(
                CdtrfSyncFilters(page_size=20), None
            )
        )



def test_transient_detail_timeout_is_retried_then_verified(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_sync as module
    calls = []

    def flaky(trade, lot, *, session):
        calls.append(lot)
        if len(calls) < 3:
            raise requests.exceptions.Timeout("temporary upstream timeout")
        return _lot(trade, lot)

    monkeypatch.setattr(module, "fetch_verified_cdtrf_lot", flaky)
    delays = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    lot = module._verified_detail_with_retry("400001", "500001", _Session())
    assert lot.external_id == "cdtrf:500001"
    assert len(calls) == 3
    assert delays == [1, 2]


def test_provider_403_is_not_retried(monkeypatch):
    import bankrotai.connectors.registry.cdtrf_sync as module
    from bankrotai.services.etp_cdtrf_canary import CdtrfCanaryError

    calls = []
    def denied(*args, **kwargs):
        calls.append(1)
        raise CdtrfCanaryError("CDTRF HTTP 403")

    monkeypatch.setattr(module, "fetch_verified_cdtrf_lot", denied)
    with pytest.raises(CdtrfCanaryError, match="403"):
        module._verified_detail_with_retry("400001", "500001", _Session())
    assert len(calls) == 1


def test_live_catalogue_total_drift_and_shifted_rows_are_nonfatal(monkeypatch):
    """A live non-reconciling catalogue must finish when page rows shift."""
    import bankrotai.connectors.registry.cdtrf_sync as module

    class Session:
        def get(self, url, *, timeout, allow_redirects, headers):
            page = 1 if "PageNum=1" in url else 2
            start = 1 if page == 1 else 20  # lot 20 overlaps after a new trade arrives
            rows = [{
                "tradeId": 400000 + i,
                "tradeLotId": 500000 + i,
                "categories": ",21,", "actualPrice": 500000,
            } for i in range(start, 21 if page == 1 else 40)]
            reply = _Reply()
            reply.json = lambda: {
                "success": True,
                "totalCount": 40 if page == 1 else 41,
                "items": rows,
            }
            return reply

    monkeypatch.setattr(
        module, "fetch_verified_cdtrf_lot",
        lambda trade, lot, *, session: _lot(trade, lot),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    crawler = CdtrfSourceCrawler(Session())
    first = asyncio.run(crawler.search(CdtrfSyncFilters(page_size=20), None))
    second = asyncio.run(crawler.search(CdtrfSyncFilters(page_size=20), first.next_cursor))

    assert len(first.items) == 20
    assert len(second.items) == 19
    assert second.metadata["catalogue_total_changes"] == 1
    assert second.metadata["catalogue_overlap_rows_skipped"] == 1
    assert second.metadata["safe_for_archival"] is False
