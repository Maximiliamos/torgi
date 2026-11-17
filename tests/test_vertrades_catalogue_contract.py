"""Vertrades catalogue contract regressions from the public mixed register."""
from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources.vertrades import (
    VertradesCardError,
    catalogue_structure_summary,
    catalogue_page_url,
    has_next_catalogue_page,
    parse_catalogue_page,
)


def _row(trade: str, lot: int, title: str, price: str, provider: int, status: str) -> str:
    filler = "".join("<td>-</td>" for _ in range(4))
    return (
        "<tr>"
        f"<td>{trade}</td><td><a href='/showTrades?id={provider}'>{lot}</a></td>"
        "<td>synthetic debtor</td>"
        f"<td>{title}</td>"
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


def _div_procedure(
    trade: str,
    provider: int,
    rows: list[tuple[int, str, str, str]],
) -> str:
    asset_rows = []
    status_rows = []
    for lot_number, title, price, status in rows:
        asset_rows.append(
            "<div class='row_lot'>"
            f"<p class='contests-lot-number'>{lot_number}</p>"
            "<p class='contests-count'>synthetic owner</p>"
            "<p class='contests_lot'>"
            f"<a href='/showTrades?id={provider}'>ЛОТ№{lot_number}(1/1) {title}</a>"
            f"<span>{price}</span>"
            "</p>"
            "</div>"
        )
        status_rows.append(
            "<div class='row_lot'>"
            "<p class='contests_start'>17.11.2026 10:00:00</p>"
            "<p class='contests_start'>24.12.2026 10:00:00</p>"
            "<p class='contests_start-date'>28.12.2026 17:00:00</p>"
            f"<p class='contests-status'>{status}</p>"
            "</div>"
        )
    return (
        "<div class='row'>"
        f"<p class='contests_num'><a href='/showTrades?id={provider}'>{trade}</a></p>"
        f"<div class='rows_lot'>{''.join(asset_rows)}</div>"
        "<p class='contests_bidder'>synthetic bidder</p>"
        f"<div class='rows_lot'>{''.join(status_rows)}</div>"
        "</div>"
    )


def _div_catalogue() -> str:
    header = (
        "<div class='row'>"
        "<p class='contests_num'>№</p>"
        "<p class='contests_bidder'>Организатор</p>"
        "</div>"
    )
    return "<html><body>" + header + "".join([
        _div_procedure(
            "150-АП",
            102288306,
            [
                (
                    1,
                    "жилое помещение пл.54,9кв.м. кад№34:34:080122:1833. "
                    "Адрес: г. Волгоград, ул. Тестовая, д. 1.",
                    "2 841 000,00",
                    "Завершенные торги",
                ),
                (
                    2,
                    "Автомобиль Ford Focus VIN TEST",
                    "450 000,00",
                    "Завершенные торги",
                ),
            ],
        ),
        _div_procedure(
            "144-АП",
            102288307,
            [
                (
                    3,
                    "земельный участок пл. 425кв.м. кад№34:10:080001:792. "
                    "Адрес: Волгоградская обл., тестовый район.",
                    "30 000,00",
                    "Торги по лоту отменены",
                ),
            ],
        ),
    ]) + "</body></html>"


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



def test_utf8_catalogue_bytes_ignore_stale_legacy_meta_charset():
    html = _catalogue().replace(
        "<html><body>",
        "<html><head><meta charset='windows-1251'></head><body>",
    ).encode("utf-8")
    lots = parse_catalogue_page(html, page=1)
    assert [lot.external_id for lot in lots] == [
        "vertrades.ru:102259678:1",
        "vertrades.ru:102259678:2",
        "vertrades.ru:102259682:4",
    ]



def test_structure_summary_is_non_sensitive_counts_only():
    summary = catalogue_structure_summary(_catalogue())
    assert summary == {
        "table_rows": 6,
        "rows_with_10_cells": 6,
        "div_procedure_rows": 0,
        "div_asset_lot_rows": 0,
        "div_status_rows": 0,
        "trade_code_rows": 6,
        "numeric_lot_rows": 6,
        "property_text_rows": 3,
        "show_trades_id_rows": 6,
    }


def test_current_div_catalogue_ignores_header_row_without_trade_link() -> None:
    html = (
        "<html><body>"
        "<div class='row'><p class='contests_num'>№</p></div>"
        + _div_procedure(
            "150-АП",
            102288306,
            [
                (
                    1,
                    "земельный участок 500 кв.м.",
                    "100 000,00",
                    "Завершенные торги",
                ),
            ],
        )
        + "</body></html>"
    )

    lots = parse_catalogue_page(html, page=1)

    assert [lot.external_id for lot in lots] == [
        "vertrades.ru:102288306:1",
    ]


def test_current_div_catalogue_pairs_asset_and_lifecycle_rows() -> None:
    lots = parse_catalogue_page(_div_catalogue(), page=1)

    assert [lot.external_id for lot in lots] == [
        "vertrades.ru:102288306:1",
        "vertrades.ru:102288307:3",
    ]
    assert [lot.start_price for lot in lots] == [2841000.0, 30000.0]
    assert [lot.auction_status for lot in lots] == ["closed", "closed"]
    assert [lot.raw_data["trade_id"] for lot in lots] == ["150-АП", "144-АП"]
    assert all(lot.raw_data["catalogue_contract"] == "div_rows_lot_v2" for lot in lots)

    assert lots[0].cadastral_number == "34:34:080122:1833"
    assert lots[0].area == 54.9
    assert lots[0].application_start_at.isoformat() == "2026-11-17T10:00:00"
    assert lots[0].application_deadline.isoformat() == "2026-12-24T10:00:00"
    assert lots[0].auction_at.isoformat() == "2026-12-28T17:00:00"


def test_current_div_structure_summary_tracks_real_rows_not_parallel_double_count() -> None:
    summary = catalogue_structure_summary(_div_catalogue())

    assert summary == {
        "table_rows": 0,
        "rows_with_10_cells": 0,
        "div_procedure_rows": 2,
        "div_asset_lot_rows": 3,
        "div_status_rows": 3,
        "trade_code_rows": 2,
        "numeric_lot_rows": 3,
        "property_text_rows": 2,
        "show_trades_id_rows": 3,
    }


def test_current_div_catalogue_fails_closed_when_parallel_rows_are_misaligned() -> None:
    lifecycle_row = (
        "<div class='row_lot'>"
        "<p class='contests_start'>17.11.2026 10:00:00</p>"
        "<p class='contests_start'>24.12.2026 10:00:00</p>"
        "<p class='contests_start-date'>28.12.2026 17:00:00</p>"
        "<p class='contests-status'>Завершенные торги</p>"
        "</div>"
    )
    html = _div_catalogue().replace(lifecycle_row, "", 1)
    with pytest.raises(VertradesCardError, match="asset_status_alignment"):
        parse_catalogue_page(html)


def test_current_div_catalogue_never_silently_truncates_property_rows() -> None:
    rows = [
        (
            index,
            f"земельный участок {index} кв.м.",
            "100 000,00",
            "Завершенные торги",
        )
        for index in range(1, 102)
    ]
    html = "<html><body>" + _div_procedure(
        "150-АП",
        102288306,
        rows,
    ) + "</body></html>"

    with pytest.raises(VertradesCardError, match="property_rows_exceed_limit"):
        parse_catalogue_page(html, limit=100)


def test_current_div_catalogue_fails_closed_on_procedure_lot_identity_mismatch() -> None:
    html = _div_catalogue().replace(
        "href='/showTrades?id=102288306'>ЛОТ№1",
        "href='/showTrades?id=999999999'>ЛОТ№1",
        1,
    )
    with pytest.raises(VertradesCardError, match="procedure_lot_identity_mismatch"):
        parse_catalogue_page(html)
