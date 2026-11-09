"""Vertrades catalogue contract regressions from the public mixed register."""
from __future__ import annotations

from bankrotai.connectors.etp_sources import PROFILES, parse_listing_html
from bankrotai.connectors.registry.direct_etp_catalogue import PROFILE_BY_ID, parse_catalogue


PROFILE = next(p for p in PROFILES if p.source_id == "vertrades.ru")
DIRECT = PROFILE_BY_ID["vertrades.ru"]


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
            "1060000.00", 102259678, "Торги завершены",
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


def test_direct_catalogue_keeps_only_proven_property_rows_and_real_trade_codes():
    items = parse_catalogue(DIRECT, _catalogue(), limit=100)
    assert [(x["trade"], x["lot"]) for x in items] == [
        ("55-АП", "1"), ("55-АП", "2"), ("150-АЗ", "4"),
    ]
    assert [x["listing_price_rub"] for x in items] == [1060000.0, 246800.0, 750000.0]
    assert all("Автомобиль" not in x["title"] for x in items)
    assert all("Право требования" not in x["title"] for x in items)
    assert all("Оборудование" not in x["title"] for x in items)


def test_profiled_catalogue_identity_is_provider_procedure_plus_lot_number():
    lots = parse_listing_html(PROFILE, _catalogue(), page=2)
    assert [x.external_id for x in lots] == [
        "vertrades.ru:102259678:1",
        "vertrades.ru:102259678:2",
        "vertrades.ru:102259682:4",
    ]
    assert [x.raw_data["trade_id"] for x in lots] == ["55-АП", "55-АП", "150-АЗ"]
    assert [x.raw_data["lot_number"] for x in lots] == ["1", "2", "4"]
    assert all(x.raw_data["listing_page"] == 2 for x in lots)
    assert lots[0].start_price == 1060000.0
    assert lots[1].start_price == 246800.0


def test_unicode_dash_trade_code_is_canonicalized():
    html = "<table>" + _row(
        "150–АП", 7, "земельный участок 500 кв.м.", "500000.00",
        102259683, "Торги завершены",
    ) + "</table>"
    item = parse_catalogue(DIRECT, html, limit=10)[0]
    assert item["trade"] == "150-АП"
