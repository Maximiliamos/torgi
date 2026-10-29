"""M-ETS pagination never mistakes catalogue listings for verified imports."""
import pytest

from bankrotai.connectors.registry.mets_public import MetsEvidenceError
from bankrotai.services.etp_mets_pagination_probe import scan_mets_pages


def _html(*identities):
    return "<html><body>" + "".join(
        f'<a href="/{number}">Продажа имущества</a>' for number in identities
    ) + "</body></html>"


class Response:
    status_code = 200
    headers = {"Content-Type": "text/html"}

    def __init__(self, html):
        self.text = html
        self.content = html.encode()


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, *, timeout, allow_redirects, headers):
        assert url.startswith("https://m-ets.ru/search?q=")
        assert not allow_redirects
        assert timeout == (5, 25)
        assert headers["Accept"] == "text/html"
        self.calls.append(url)
        return Response(self.pages[len(self.calls) - 1])


def test_first_three_distinct_pages_and_no_source_mutations():
    session = FakeSession([
        _html("243003-1", "243004-1"),
        _html("243005-1"),
        _html(),
    ])
    delays = []
    report = scan_mets_pages(
        session, max_pages=5, delay_seconds=1, sleep=delays.append,
    )
    assert len(session.calls) == 3
    assert session.calls[0].count("&page=") == 0
    assert session.calls[1].endswith("&page=2")
    assert session.calls[2].endswith("&page=3")
    assert delays == [1, 1]
    assert report["unique_catalogue_lot_ids"] == 3
    assert report["complete_source_inventory"] is False
    assert report["detail_validation_proven"] is False
    assert report["database_writes"] == 0
    assert report["archival_allowed"] is False
    assert report["map_publication"] is False
    assert "243003" not in str(report)
    assert "Продажа имущества" not in str(report)


def test_repeated_lot_identity_across_pages_is_rejected():
    session = FakeSession([_html("243003-1"), _html("243003-1")])
    with pytest.raises(MetsEvidenceError, match="overlapping"):
        scan_mets_pages(session, max_pages=2, sleep=lambda _: None)


def test_empty_first_page_rejected_not_treated_as_complete():
    with pytest.raises(MetsEvidenceError, match="first page"):
        scan_mets_pages(FakeSession([_html()]), max_pages=1)


@pytest.mark.parametrize("pages,delay", [(0, 1), (6, 1), (2, 0), (2, 11)])
def test_bounded_page_rate_limits(pages, delay):
    with pytest.raises(ValueError):
        scan_mets_pages(FakeSession([]), max_pages=pages, delay_seconds=delay)
