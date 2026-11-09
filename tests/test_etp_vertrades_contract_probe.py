"""Vertrades contract probe is bounded, read-only and digest based."""
from __future__ import annotations

import pytest

from bankrotai.services.etp_vertrades_contract_probe import (
    VertradesProbeError,
    probe_vertrades_contract,
    probe_vertrades_detail_sample,
)


def _row(trade: int, provider: int, lot: int = 1) -> str:
    return (
        "<tr>"
        f"<td>{trade}-АП</td><td><a href='/showTrades?id={provider}'>{lot}</a></td>"
        "<td>synthetic</td><td>земельный участок 600 кв.м.</td>"
        "<td>500000.00</td><td>-</td><td>-</td><td>-</td><td>-</td>"
        "<td>Завершенные торги</td>"
        "</tr>"
    )


def _page(page: int, trade: int, provider: int, *, last: bool = False) -> str:
    next_link = "" if last else f"<a href='/bidding?page={page + 1}'>{page + 1}</a>"
    search_form = """
    <form method="get" action="/bidding">
      <label>№ торгов:</label><input name="trade_number">
      <label>Наименование лота:</label><input name="lot_name">
      <select name="status"></select>
    </form>
    """
    return (
        "<html><body>" + search_form + "<table>" + _row(trade, provider) +
        "</table>" + next_link + "</body></html>"
    )


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
    assert report["filter_form"] == {
        "method": "get",
        "action_path": "/bidding",
        "field_names": ["lot_name", "status", "trade_number"],
    }
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



DETAIL_NUMBERS = (
    1, 2, 3, 4, 5, 6, 7, 9, 10, 11,
    12, 13, 14, 15, 16, 17, 18, 19, 21, 22,
    23, 24, 25, 26, 27, 28, 29, 33, 34, 35,
)


def _detail_html(numbers=DETAIL_NUMBERS) -> str:
    sections = []
    for number in numbers:
        status = "Торги по лоту отменены" if number == 27 else "Торги завершены"
        price = 100_000 + number * 1_000
        sections.append(f"""
        <h3>Сведения по лоту №{number}</h3>
        <table>
          <tr><td>Статус торгов по лоту</td><td>{status}</td></tr>
          <tr><td>Краткие сведения об имуществе (наименование лота)</td>
              <td>земельный участок {500 + number} кв.м. кадастровый номер 34:34:000001:{1000 + number}</td></tr>
          <tr><td>Cведения об арестованном имуществе, реализуемом на торгах</td>
              <td>земельный участок {500 + number} кв.м. кадастровый номер 34:34:000001:{1000 + number}.
              Адрес: г. Тест, д.{number}. Начальная цена: {price} руб.
              Задаток: 10 000 руб. Ограничения: запрет регистрации.</td></tr>
          <tr><td>Начальная цена продажи имущества</td><td>{price},00 НДС не облагается</td></tr>
          <tr><td>Размер задатка</td><td>10 000,00</td></tr>
        </table>
        """)
    return """
    <html><body>
    <h3>идентификационный номер: 150-АП</h3>
    <table>
      <tr><td>Начало предоставления заявок на участие</td><td>24.08.2022 08:00</td></tr>
      <tr><td>Окончание предоставления заявок на участие</td><td>19.09.2022 23:59</td></tr>
      <tr><td>Начало подачи предложений о цене имущества</td><td>23.09.2022 10:00</td></tr>
    </table>
    """ + "".join(sections) + "</body></html>"


def test_live_detail_probe_contract_checks_thirty_sections_from_one_response():
    session = Session([_detail_html()])
    report = probe_vertrades_detail_sample(session)
    assert len(session.calls) == 1
    assert report["details_checked"] == 30
    assert report["unique_lot_numbers"] == 30
    assert report["price_verified"] == 30
    assert report["status_verified"] == 30
    assert report["cadastre_present"] == 30
    assert report["address_present"] == 30
    assert report["area_present"] == 30
    assert report["dates_verified"] == 30
    assert len(report["detail_digest_sha256"]) == 64
    assert report["database_writes"] == 0
    assert report["reconciliation_allowed"] is False
    assert report["archival_allowed"] is False
    assert report["production_enabled"] is False
    assert "земельный" not in str(report)
    assert "Тест" not in str(report)


def test_live_detail_probe_missing_requested_section_fails_closed():
    with pytest.raises(VertradesProbeError, match="detail_lot_35_failed"):
        probe_vertrades_detail_sample(Session([_detail_html(DETAIL_NUMBERS[:-1])]))


def test_live_detail_probe_requires_at_least_thirty_unique_numbers():
    with pytest.raises(ValueError, match="at least 30"):
        probe_vertrades_detail_sample(
            Session([]),
            lot_numbers=tuple(range(1, 30)),
        )
