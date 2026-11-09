"""Sanitized synthetic fixtures: never copy provider PII into tests."""
import pytest

from bankrotai.connectors.etp_sources.vertrades_detail_audit import (
    VertradesDetailContractError,
    inspect_shared_procedure,
)


def _html(*sections):
    return "<html><body>" + "".join(sections) + "</body></html>"


def _lot(number, *, status="Торги завершены", price="1 234 567,00"):
    return (
        f"<h3>Сведения по лоту №{number}</h3>"
        "<table>"
        f"<tr><td>Статус торгов по лоту</td><td>{status}</td></tr>"
        "<tr><td>Размер задатка</td><td>999 999,00</td></tr>"
        f"<tr><td>Начальная цена продажи имущества</td><td>{price} НДС не облагается</td></tr>"
        "</table>"
    )


def test_prices_and_status_are_isolated_per_lot():
    rows = inspect_shared_procedure(_html(
        _lot(1, price="1 234 567,00"),
        _lot(2, status="Торги по лоту отменены", price="4 000 000,50"),
    ))
    assert rows == [
        {"lot_number": 1, "source_status": "Торги завершены", "initial_price_rub": "1234567,00"},
        {"lot_number": 2, "source_status": "Торги по лоту отменены", "initial_price_rub": "4000000,50"},
    ]


def test_no_cross_lot_fallback_for_missing_price():
    rows = inspect_shared_procedure(_html(_lot(1), _lot(2).replace(
        "<td>Начальная цена продажи имущества</td>", "<td>Размер задатка</td>"
    )))
    assert rows[0]["initial_price_rub"] == "1234567,00"
    assert rows[1]["initial_price_rub"] is None


def test_duplicate_lot_headings_fail_closed():
    with pytest.raises(VertradesDetailContractError, match="Duplicate"):
        inspect_shared_procedure(_html(_lot(1), _lot(1)))


def test_missing_explicit_lot_headings_fail_closed():
    with pytest.raises(VertradesDetailContractError, match="No explicit"):
        inspect_shared_procedure("<table><tr><td>Цена</td><td>100,00</td></tr></table>")


def test_duplicate_price_field_is_not_verified():
    rows = inspect_shared_procedure(_html(
        _lot(3).replace("</table>", "<tr><td>Начальная цена продажи имущества</td><td>1 000 000,00</td></tr></table>")
    ))
    assert rows[0]["initial_price_rub"] is None


def test_size_guard():
    with pytest.raises(VertradesDetailContractError, match="bounded"):
        inspect_shared_procedure("x" * 2_000_001)
