from __future__ import annotations

from pathlib import Path

import pytest

from bankrotai.connectors.registry.mets_public import APARTMENTS_URL
from bankrotai.services.etp_mets_full_catalogue import (
    MetsCatalogueScanError,
    run_two_independent_passes,
    scan_complete_mets_catalogue,
)


def test_windows_runner_imports_scanner_from_checked_out_source() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "run-mets-full-readonly-no-vpn.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "$sourceRoot = Join-Path $repo 'src'" in script
    assert "$env:PYTHONPATH = $sourceRoot" in script
    assert "git.exe -C $repo rev-parse HEAD" in script
    assert "Remove-Item Env:PYTHONPATH" in script


def _card(
    identity: str,
    *,
    price: str = "100.00",
    status: str = "Торги в стадии приема заявок",
    title: str | None = None,
) -> str:
    trade, lot = identity.split("-")
    title = title or f"Имущество {identity}"
    return f"""
    <div class="card-so" itemprop="offers">
      <a class="search-comp-item" itemprop="url" href="{identity}">Открыть</a>
      <div class="comp-info-description">
        <div class="comp-title" itemprop="name">{title}</div>
        <div class="comp-regnumber">{trade}-МЭТС, Лот №{lot}</div>
        <div class="comp-type">Торги по банкротству. {status}</div>
      </div>
      <div itemprop="price" content="{price}">{price}</div>
    </div>
    """


def _html(
    page: int,
    identities: list[str],
    *,
    total: int,
    next_page: int | None,
    categories: tuple[str, ...] = ("36", "34", "39", "37", "38", "28", "41"),
    statuses: tuple[str, ...] = ("0", "1"),
    prices: tuple[str, ...] | None = None,
    next_href: str | None = None,
    selected_page_visible: bool = True,
) -> str:
    price_values = prices or tuple("100.00" for _ in identities)
    category_options = "".join(
        f'<option value="{value}" selected>{value}</option>' for value in categories
    )
    status_options = "".join(
        f'<option value="{value}" selected>{value}</option>' for value in statuses
    )
    next_link = ""
    if next_page is not None:
        next_href = next_href or (
            f"/search?q=eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiwzNCwzOSwzNywzOCwyOCw0MSJ9"
            f"&amp;page={next_page}"
        )
        next_link = (
            '<a class="next link" aria-label="Следующая страница" '
            f'href="{next_href}"></a>'
        )
    page_marker = (
        f'<span class="active">{page}</span>'
        if selected_page_visible
        else f"<span>{page}</span>"
    )
    return f"""
    <html><head><meta itemprop="offerCount" content="{total}"></head><body>
      <select name="search_category" multiple>{category_options}</select>
      <select name="stat" multiple>{status_options}</select>
      <div class="search-results-info"><div class="value">{total}</div></div>
      <div class="paginator">{page_marker}{next_link}</div>
      {''.join(_card(identity, price=price) for identity, price in zip(identities, price_values))}
    </body></html>
    """


class _Response:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=UTF-8"}

    def __init__(self, html: str, url: str) -> None:
        self.text = html
        self.content = html.encode()
        self.url = url


class _Session:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.closed = False

    def get(self, url, *, timeout, allow_redirects, headers):
        assert timeout == (5, 25)
        assert allow_redirects is False
        assert headers["Accept"] == "text/html"
        assert headers["User-Agent"] == "TORGI-METS-full-readonly-audit/1"
        self.calls.append(url)
        return _Response(self.pages[len(self.calls) - 1], url)

    def close(self) -> None:
        self.closed = True


def _good_pages(*, last_price: str = "300.00") -> list[str]:
    return [
        _html(1, ["244381-1", "244360-1"], total=3, next_page=2),
        _html(2, ["215028-291"], total=3, next_page=None, prices=(last_price,)),
    ]


def test_full_selected_catalogue_walk_proves_incomplete_terminal_page() -> None:
    session = _Session(_good_pages())
    report = scan_complete_mets_catalogue(
        session, max_pages=10, delay_seconds=0,
    )
    assert session.calls == [APARTMENTS_URL, f"{APARTMENTS_URL}&page=2"]
    assert report["reported_total"] == 3
    assert report["unique_lot_ids"] == 3
    assert report["last_page"] == 2
    assert report["last_page_proven"] is True
    assert report["complete_selected_filter_inventory"] is True
    assert report["catalogue_prices_verified"] is True
    assert report["catalogue_statuses_verified"] is True
    assert report["complete_source_inventory"] is False
    assert report["category_scope_complete"] is False
    assert report["omitted_property_category_ids"] == ["11", "40"]
    assert report["detail_validation_proven"] is False
    assert report["production_database_writes"] == 0
    assert report["archival_allowed"] is False
    assert report["reconcile_missing"] is False
    assert report["geocoding"] is False
    assert report["map_publication"] is False
    assert "244381-1" not in str(report)


def test_raw_first_page_may_omit_active_marker_but_later_page_may_not() -> None:
    pages = _good_pages()
    pages[0] = _html(
        1,
        ["244381-1", "244360-1"],
        total=3,
        next_page=2,
        selected_page_visible=False,
    )
    report = scan_complete_mets_catalogue(
        _Session(pages), max_pages=10, delay_seconds=0,
    )
    assert report["last_page_proven"] is True

    pages[1] = _html(
        2,
        ["215028-291"],
        total=3,
        next_page=None,
        selected_page_visible=False,
    )
    with pytest.raises(
        MetsCatalogueScanError,
        match=r'page 2 is not visibly selected; paginator=.*"active_count": 0',
    ):
        scan_complete_mets_catalogue(_Session(pages), delay_seconds=0)


def test_two_matching_independent_passes_close_sessions() -> None:
    sessions = [_Session(_good_pages()), _Session(_good_pages())]
    report = run_two_independent_passes(
        lambda: sessions.pop(0), max_pages=10, delay_seconds=0,
    )
    assert report["passes_match"] is True
    assert report["last_page_proven_twice"] is True
    assert report["filters_proven_twice"] is True
    assert report["catalogue_prices_and_statuses_proven_twice"] is True
    assert report["complete_selected_filter_inventory_twice"] is True
    assert report["complete_source_inventory"] is False
    assert sessions == []


def test_two_passes_include_price_and_status_in_stability_digest() -> None:
    sessions = [
        _Session(_good_pages(last_price="300.00")),
        _Session(_good_pages(last_price="301.00")),
    ]
    report = run_two_independent_passes(
        lambda: sessions.pop(0), max_pages=10, delay_seconds=0,
    )
    assert report["passes_match"] is False
    assert report["last_page_proven_twice"] is False


def test_cross_page_duplicate_identity_is_rejected() -> None:
    pages = [
        _html(1, ["244381-1", "244360-1"], total=3, next_page=2),
        _html(2, ["244381-1"], total=3, next_page=None),
    ]
    with pytest.raises(MetsCatalogueScanError, match="cross-page duplicate"):
        scan_complete_mets_catalogue(_Session(pages), delay_seconds=0)


@pytest.mark.parametrize(
    "categories,statuses,match",
    [
        (("36", "34"), ("0", "1"), "category filter"),
        (("36", "34", "39", "37", "38", "28", "41"), ("1",), "status filter"),
    ],
)
def test_selected_filter_mismatch_is_rejected(categories, statuses, match) -> None:
    page = _html(
        1, ["244381-1"], total=1, next_page=None,
        categories=categories, statuses=statuses,
    )
    with pytest.raises(MetsCatalogueScanError, match=match):
        scan_complete_mets_catalogue(_Session([page]), delay_seconds=0)


def test_next_link_must_preserve_exact_filter_and_page() -> None:
    bad = _html(
        1,
        ["244381-1", "244360-1"],
        total=3,
        next_page=2,
        next_href="/search?q=different&page=2",
    )
    with pytest.raises(MetsCatalogueScanError, match="does not preserve"):
        scan_complete_mets_catalogue(_Session([bad]), delay_seconds=0)


def test_full_sized_terminal_page_is_not_claimed_as_proven() -> None:
    pages = [
        _html(1, ["244381-1", "244360-1"], total=4, next_page=2),
        _html(2, ["215028-291", "215028-294"], total=4, next_page=None),
    ]
    with pytest.raises(MetsCatalogueScanError, match="incomplete final page"):
        scan_complete_mets_catalogue(_Session(pages), delay_seconds=0)


def test_identity_registration_and_catalogue_status_are_fail_closed() -> None:
    bad_identity = _good_pages()[0].replace(
        "244381-МЭТС, Лот №1", "244382-МЭТС, Лот №1", 1
    )
    with pytest.raises(MetsCatalogueScanError, match="registration contradicts"):
        scan_complete_mets_catalogue(_Session([bad_identity]), delay_seconds=0)

    bad_status = _good_pages()[0].replace(
        "Торги в стадии приема заявок", "Торги завершены", 1
    )
    with pytest.raises(MetsCatalogueScanError, match="unexpected selected-scope status"):
        scan_complete_mets_catalogue(_Session([bad_status]), delay_seconds=0)
