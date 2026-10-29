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
        assert timeout == (3, 8)
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
    assert "900" not in str(report)


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
