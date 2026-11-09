"""Vertrades catalogue contract regressions from the public mixed register."""
from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources.vertrades import (
    VertradesCardError,
    catalogue_page_url,
    has_next_catalogue_page,
    parse_catalogue_page,
)


def _row(trade: str, lot: int, title: str, price: str, provider: int, status: str) -> str:
    filler = "".join("<td>-</td>" for _ in range(4))
    return (
        "<tr>"
        f"<td>{trade}</td><td>{lot}</td><td>synthetic debtor</td>"
        f"<td><a href='/showTrades?id={provider}'>{title}</a></td>"
        f"<td>{price}</td>{filler}<td>{status}</td>"
        "</tr>"
    )


def _catalogue() -> str:
    return "<html><body><table>" + "".join([
        _row(
            "55-АП", 1,
            "жилое здание 29,6 кв.м., кадастровый номер 34:02:010003:2648",
            "1060000.00", 102259678, "Завершенные торги",
        ),
        _row(
            "55-АП", 2,
            "нежилое здание 9 кв.м., кадастровый номер 34:34:080147:2677",
            "246800.00", 102259678, "Торги по лоту отменены",
        ),
        _row(
            "56-АП", 1, "Автомобиль Ford Focus VIN TEST", "450000.00",
            102259679, "Торги завершены",
        ),
        _row(
            "57-АП", 1, "Право требования задолженности", "100000.00",
            102259680, "Торги завершены",
        ),
        _row(
            "149-АЗ", 3, "Оборудование: станок токарный", "300000.00",
            102259681, "Торги завершены",
        ),
        _row(
            "150-АЗ", 4, "земельный участок 1200 кв.м.", "750000.00",
            102259682, "Торги завершены",
        ),
    ]) + "</table></body></html>"


def test_catalogue_keeps_only_proven_property_rows_and_composite_identity():
    lots = parse_catalogue_page(_catalogue(), page=2)
    assert [x.external_id for x in lots] == [
        "vertrades.ru:102259678:1",
        "vertrades.ru:102259678:2",
        "vertrades.ru:102259682:4",
    ]
    assert [x.raw_data["trade_id"] for x in lots] == ["55-АП", "55-АП", "150-АЗ"]
    assert [x.raw_data["lot_number"] for x in lots] == ["1", "2", "4"]
    assert all(x.raw_data["listing_page"] == 2 for x in lots)
    assert [x.start_price for x in lots] == [1060000.0, 246800.0, 750000.0]
    assert lots[0].cadastral_number == "34:02:010003:2648"
    assert lots[1].cadastral_number == "34:34:080147:2677"
    assert lots[0].auction_status == "closed"
    assert lots[1].auction_status == "closed"


def test_unicode_dash_trade_code_is_canonicalized():
    html = "<table>" + _row(
        "150–АП", 7, "земельный участок 500 кв.м.", "500000.00",
        102259683, "Торги завершены",
    ) + "</table>"
    lot = parse_catalogue_page(html, limit=10)[0]
    assert lot.raw_data["trade_id"] == "150-АП"


def test_duplicate_individual_identity_fails_closed():
    row = _row(
        "55-АП", 1, "жилое здание 29,6 кв.м.", "1060000.00",
        102259678, "Завершенные торги",
    )
    with pytest.raises(VertradesCardError, match="duplicate_individual"):
        parse_catalogue_page("<table>" + row + row + "</table>")


def test_page_urls_and_terminal_signal_require_explicit_same_origin_next_page():
    assert catalogue_page_url(1) == "https://arest.vertrades.ru/bidding"
    assert catalogue_page_url(4).endswith("?page=4")
    assert has_next_catalogue_page(
        "<a href='/bidding?page=2'>2</a><a href='https://evil.example/bidding?page=2'>x</a>",
        1,
    ) is True
    assert has_next_catalogue_page("<a href='/bidding?page=4'>4</a>", 4) is False
