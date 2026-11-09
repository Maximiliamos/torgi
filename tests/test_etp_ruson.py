from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bankrotai.connectors.etp_sources import PROFILES, parse_detail_html


FIXTURES = Path(__file__).parent / "fixtures" / "etp" / "ruson"


def _profile():
    return next(item for item in PROFILES if item.source_id == "rus-on.ru")


def _parse(name: str, lot_id: str, *, observed_at_utc: datetime | None = None):
    return parse_detail_html(
        _profile(),
        (FIXTURES / name).read_text(encoding="utf-8"),
        detail_url=f"https://rus-on.ru/bankrot/lot_view.php?lot_nid={lot_id}",
        external_id=f"rus-on.ru:{lot_id}",
        observed_at_utc=observed_at_utc,
    )


def test_ruson_ordinary_auction_uses_structured_lot_fields_not_page_boilerplate() -> None:
    lot = _parse("ordinary_active.html", "900001")
    assert lot.external_id == "rus-on.ru:900001"
    assert lot.title == "Квартира"
    assert lot.category == "real_estate"
    assert lot.cadastral_number == "78:36:0005511:8632"
    assert lot.area == 38.3
    assert lot.address == "г. Санкт-Петербург, улица Тестовая, д. 13"
    assert lot.start_price == 7_415_000
    assert lot.current_price is None
    assert lot.deposit_amount == 1_483_000
    assert lot.auction_step_amount == 370_750
    assert lot.auction_status == "scheduled"
    assert lot.auction_type == "auction"
    assert lot.application_start_at == datetime(2026, 11, 16, 10, 0)
    assert lot.application_deadline == datetime(2026, 12, 14, 10, 0)
    assert lot.auction_at == datetime(2026, 12, 21, 10, 0)
    assert lot.auction_timezone == "Europe/Moscow"
    assert lot.raw_data["ruson_initial_price_had_currency"] is False
    assert lot.raw_data["ruson_detail_template"] == "auction"


def test_ruson_public_offer_derives_current_price_only_from_active_period() -> None:
    lot = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 9, 1, 0),
    )
    assert lot.external_id == "rus-on.ru:900002"
    assert lot.auction_type == "public_offer"
    assert lot.auction_status == "active"
    assert lot.start_price == 325_802.50
    assert lot.current_price == 293_222.25
    assert lot.next_interval_price == 278_561.14
    assert lot.next_price_reduction_at == datetime(2026, 11, 11, 11, 0)
    assert lot.public_offer_schedule is not None
    assert [row["price"] for row in lot.public_offer_schedule] == [
        325_802.50, 293_222.25, 278_561.14,
    ]
    assert lot.raw_data["ruson_current_price_source"] == "public_offer_schedule"
    assert lot.raw_data["ruson_initial_price_format"] == "decimal_dot"


def test_ruson_ended_detail_accepts_currency_suffix_comma_decimal_without_inventing_current_price() -> None:
    lot = _parse(
        "public_offer_ended_currency.html",
        "900003",
        observed_at_utc=datetime(2026, 11, 9, 1, 0),
    )
    assert lot.start_price == 4_673_438.98
    assert lot.current_price is None
    assert lot.auction_status == "closed"
    assert lot.auction_type == "public_offer"
    assert lot.raw_data["ruson_initial_price_had_currency"] is True
    assert lot.raw_data["ruson_initial_price_format"] == "decimal_comma"


def test_ruson_vehicle_is_not_false_real_estate_because_page_contains_real_estate_label() -> None:
    lot = _parse("vehicle_active.html", "900004")
    assert lot.category == "vehicle"
    assert lot.vin == "XUUCD26UJC0002820"
    assert lot.start_price == 850_000
    assert lot.raw_data["ruson_source_categories"] == "Транспортные средства"


def test_ruson_public_offer_end_minute_is_inclusive_without_one_minute_price_gap() -> None:
    before_boundary = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 11, 10, 59, 59),
    )
    at_boundary = _parse(
        "public_offer_active.html",
        "900002",
        observed_at_utc=datetime(2026, 11, 11, 11, 0, 0),
    )
    assert before_boundary.current_price == 293_222.25
    assert at_boundary.current_price == 278_561.14
