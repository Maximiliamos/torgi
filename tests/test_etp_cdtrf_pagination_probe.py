"""CDTRF multi-page evidence must not accidentally become an importer."""
from bankrotai.services.etp_cdtrf_pagination_probe import scan_cdtrf_pages


def _row(identity: int) -> dict:
    return {
        "tradeId": identity, "tradeLotId": identity + 500,
        "categories": ",21,", "regionId": 32,
        "actualPrice": 675000, "tradeStatusDescription": "Объявлены",
    }


class Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    content = b'{"success":true}'

    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return {
            "success": True, "totalCount": 5, "items": self.rows,
        }


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, *, allow_redirects, timeout, headers):
        assert url.startswith("https://torgi.cdtrf.ru/api/Trade/trades?")
        assert "PageSize=20" in url
        assert allow_redirects is False
        assert timeout == (10, 30)
        assert "json" in headers["Accept"]
        self.calls.append(url)
        return Response(self.pages[len(self.calls) - 1])


def test_three_distinct_catalogue_pages_summarized_without_pii_or_writes():
    delays = []
    session = Session([[_row(900), _row(901)], [_row(902)], []])
    report = scan_cdtrf_pages(
        session, max_pages=5, delay_seconds=1, sleep=delays.append,
    )
    assert len(session.calls) == 3
    assert delays == [1, 1]
    assert report["pages_scanned"] == 3
    assert report["unique_catalogue_lot_ids"] == 3
    assert report["property_hint_candidates"] == 3
    assert report["complete_source_inventory"] is False
    assert report["detail_validation_proven"] is False
    assert report["production_database_writes"] == 0
    assert report["archival_allowed"] is False
    assert "tradeId" not in str(report)
    assert "trade_lot_id" not in str(report)
    assert "detail_url" not in str(report)


def test_overlapping_lot_identity_across_pages_fails_closed():
    import pytest

    session = Session([[_row(900)], [_row(900)]])
    with pytest.raises(ValueError, match="overlapping"):
        scan_cdtrf_pages(session, max_pages=2, sleep=lambda seconds: None)


def test_strict_page_and_delay_limits():
    import pytest

    for pages, delay in [(0, 1), (6, 1), (5, 0.1), (5, 100)]:
        with pytest.raises(ValueError):
            scan_cdtrf_pages(Session([]), max_pages=pages, delay_seconds=delay)


# The full-catalogue walker is separate from the bounded five-page pilot.
from urllib.parse import parse_qs, urlsplit

from bankrotai.services.etp_cdtrf_full_catalogue import (
    CdtrfCatalogueScanError, scan_complete_cdtrf_catalogue,
)


class FullScanSession:
    def __init__(self, total, server_cap=None, overlap=False, drift=False):
        self.total = total
        self.server_cap = server_cap
        self.overlap = overlap
        self.drift = drift
        self.calls = []

    def get(self, url, *, allow_redirects, timeout, headers):
        params = parse_qs(urlsplit(url).query)
        assert params["Categories"] == ["21,22,23,31,32,33,34"]
        assert all(params[x] == ["true"] for x in (
            "Declare", "RecieveReq", "DefinePart", "TradeGo", "SummingUp"
        ))
        size = self.server_cap or int(params["PageSize"][0])
        page = int(params["PageNum"][0])
        assert not allow_redirects and headers["Accept"] == "application/json"
        self.calls.append(page)
        rows = [
            {"tradeId": 390000 + i,
             "tradeLotId": 500000 + (1 if self.overlap and page == 2 and i == size + 1 else i),
             "categories": ",21,", "actualPrice": 670000}
            for i in range((page - 1) * size + 1, min(page * size, self.total) + 1)
        ]
        response = Response(rows)
        response.json = lambda: {
            "success": True,
            "totalCount": self.total + int(self.drift and page == 2),
            "items": rows,
        }
        return response


def test_full_catalogue_supports_actual_ui_sizes_without_importing():
    for size, total, pages in ((20, 105, 6), (50, 105, 3), (100, 205, 3)):
        session = FullScanSession(total)
        report = scan_complete_cdtrf_catalogue(
            session, page_size=size, sleep=lambda delay: None,
        )
        assert session.calls == list(range(1, pages + 1))
        assert report["unique_catalogue_lot_ids"] == total
        assert report["selected_catalogue_pages_observed"] is True
        assert report["complete_source_inventory"] is False
        assert report["stable_point_in_time_snapshot"] is False
        assert report["archival_allowed"] is False
        assert report["production_database_writes"] == 0


def test_full_catalogue_rejects_unsupported_1000_or_silent_server_cap():
    import pytest
    with pytest.raises(CdtrfCatalogueScanError, match="20, 50, 100"):
        scan_complete_cdtrf_catalogue(FullScanSession(205), page_size=1000)
    with pytest.raises(CdtrfCatalogueScanError, match="Incomplete page"):
        scan_complete_cdtrf_catalogue(
            FullScanSession(205, server_cap=20), page_size=100,
        )


def test_full_catalogue_detects_overlaps_drift_and_missing_pages():
    import pytest
    with pytest.raises(CdtrfCatalogueScanError, match="Overlapping"):
        scan_complete_cdtrf_catalogue(
            FullScanSession(205, overlap=True), page_size=100,
            sleep=lambda delay: None,
        )
    with pytest.raises(CdtrfCatalogueScanError, match="total changed"):
        scan_complete_cdtrf_catalogue(
            FullScanSession(205, drift=True), page_size=100,
            sleep=lambda delay: None,
        )
    with pytest.raises(CdtrfCatalogueScanError, match="page budget"):
        scan_complete_cdtrf_catalogue(
            FullScanSession(205), page_size=100, max_pages=2,
        )
