"""Vertrades contract probe is bounded, read-only and digest based."""
from __future__ import annotations

import pytest

from bankrotai.services.etp_vertrades_contract_probe import (
    VertradesProbeError,
    probe_vertrades_contract,
)


def _row(trade: int, provider: int, lot: int = 1) -> str:
    return (
        "<tr>"
        f"<td>{trade}-АП</td><td>{lot}</td><td>synthetic</td>"
        f"<td><a href='/showTrades?id={provider}'>земельный участок 600 кв.м.</a></td>"
        "<td>500000.00</td><td>-</td><td>-</td><td>-</td><td>-</td>"
        "<td>Завершенные торги</td>"
        "</tr>"
    )


def _page(page: int, trade: int, provider: int, *, last: bool = False) -> str:
    next_link = "" if last else f"<a href='/bidding?page={page + 1}'>{page + 1}</a>"
    return "<html><body><table>" + _row(trade, provider) + "</table>" + next_link + "</body></html>"


class Response:
    status_code = 200
    headers = {"Content-Type": "text/html"}

    def __init__(self, html: str):
        self.content = html.encode()
        self.text = html


class Session:
    def __init__(self, pages: list[str]):
        self.pages = pages
        self.calls: list[tuple[str, bool]] = []

    def get(self, url, *, timeout, allow_redirects, headers):
        assert timeout == (5, 25)
        assert allow_redirects is False
        assert headers["Accept"] == "text/html"
        self.calls.append((url, allow_redirects))
        return Response(self.pages[len(self.calls) - 1])


def _stable_pages() -> list[str]:
    one_pass = [
        _page(1, 150, 102288306),
        _page(2, 108, 102280484),
        _page(3, 69, 102270000),
        _page(4, 27, 102255539, last=True),
    ]
    return one_pass + one_pass


def test_two_pass_probe_proves_terminal_and_stable_digest_without_raw_data():
    session = Session(_stable_pages())
    delays = []
    report = probe_vertrades_contract(
        session, max_pages=4, passes=2, delay_seconds=0.5, sleep=delays.append,
    )
    assert len(session.calls) == 8
    assert session.calls[0][0] == "https://arest.vertrades.ru/bidding"
    assert session.calls[1][0].endswith("?page=2")
    assert session.calls[3][0].endswith("?page=4")
    assert session.calls[4][0] == "https://arest.vertrades.ru/bidding"
    assert report["terminal_page"] == 4
    assert report["terminal_page_proven"] is True
    assert report["pages_per_pass"] == 4
    assert report["unique_property_lot_ids"] == 4
    assert report["stable_between_passes"] is True
    assert len(report["id_digest_sha256"]) == 64
    assert report["database_writes"] == 0
    assert report["reconciliation_allowed"] is False
    assert report["archival_allowed"] is False
    assert report["production_enabled"] is False
    assert "synthetic" not in str(report)
    assert "102288306" not in str(report)


def test_digest_change_between_passes_fails_closed():
    first = [
        _page(1, 150, 102288306),
        _page(2, 108, 102280484),
        _page(3, 69, 102270000),
        _page(4, 27, 102255539, last=True),
    ]
    second = [
        _page(1, 150, 102288306),
        _page(2, 108, 102280484),
        _page(3, 70, 102270001),
        _page(4, 27, 102255539, last=True),
    ]
    with pytest.raises(VertradesProbeError, match="digest_changed"):
        probe_vertrades_contract(
            Session(first + second), max_pages=4, passes=2,
            delay_seconds=0.5, sleep=lambda _: None,
        )


def test_missing_property_page_is_not_mistaken_for_terminal():
    pages = [
        _page(1, 150, 102288306),
        "<html><body><a href='/bidding?page=3'>3</a></body></html>",
    ]
    with pytest.raises(VertradesProbeError, match="no_property_lot_ids_on_page:2"):
        probe_vertrades_contract(
            Session(pages), max_pages=4, passes=2,
            delay_seconds=0.5, sleep=lambda _: None,
        )


@pytest.mark.parametrize(
    "max_pages,passes,delay",
    [(1, 2, 1), (5, 2, 1), (4, 1, 1), (4, 3, 1), (4, 2, 0.1), (4, 2, 11)],
)
def test_probe_bounds(max_pages, passes, delay):
    with pytest.raises(ValueError):
        probe_vertrades_contract(
            Session([]), max_pages=max_pages, passes=passes,
            delay_seconds=delay, sleep=lambda _: None,
        )
