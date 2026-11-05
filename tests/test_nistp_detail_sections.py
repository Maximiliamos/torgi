"""Regression tests from NISTP's shared 10-lot trade evidence, 05 Nov 2026.

The source fixture is sanitized and synthetic HTML, reconstructed from the
browser-visible text. Real provider DOM is NOT yet independently verified.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bankrotai.connectors.etp_sources.nistp_detail_sections import (
    NistpCardError,
    get_nistp_lot,
    parse_nistp_shared_trade_card,
)


# Independently labelled values extracted from the user-supplied public card.
# No session cookies, organizer contact details or debtor account data.
CASES = [
    (1, "77:01:0004002:6638", "13,3", "3 072 000.00", "307 200.00"),
    (2, "77:01:0004002:6616", "11,6", "2 724 000.00", "272 400.00"),
    (3, "77:01:0004002:6699", "13,6", "3 135 000.00", "313 500.00"),
    (4, "77:01:0004002:6614", "14,3", "3 274 000.00", "327 400.00"),
    (5, "77:01:0004002:6633", "13,3", "3 274 000.00", "327 400.00"),
    (6, "77:01:0004002:6673", "12,9", "2 991 000.00", "299 100.00"),
    (7, "77:01:0004002:6613", "12,5", "2 909 000.00", "290 900.00"),
    (8, "77:01:0004002:6690", "13,9", "3 194 000.00", "319 400.00"),
    (9, "77:01:0004002:6632", "12,7", "2 950 000.00", "295 000.00"),
    (10, "77:01:0004002:6641", "15,6", "3 536 000.00", "353 600.00"),
]


NATIVE_LOT_IDS = {
    1: 502964, 2: 502965, 3: 502966, 4: 502967, 5: 502969,
    6: 502970, 7: 502971, 8: 502972, 9: 502973, 10: 502974,
}


def _section(case: tuple[int, str, str, str, str]) -> str:
    # Matches the provider's real table[id=table_lot_N] and named anchors.
    num, cadastre, area, price, deposit = case
    native_id = NATIVE_LOT_IDS[num]
    return f"""
    <table class="data" id="table_lot_{num}">
      <thead><tr><th colspan="2" class="table_header">
        <a name="lot{num}"></a><a name="lot{native_id}"></a>Лот № {num}
      </th></tr></thead>
      <tbody>
        <tr><td class="label">Номер лота</td><td>{num}</td></tr>
        <tr><td class="label">Наименование имущества</td><td>Машино-место</td></tr>
        <tr><td class="label">Cведения об имуществе (предприятии) должника,
          выставляемом на торги, его составе, характеристиках, описание</td>
          <td>Машино-место, площадью {area} кв. м, кад. № {cadastre}</td></tr>
        <tr><td class="label">Местонахождение имущества</td><td>г. Москва</td></tr>
        <tr><td class="label">Начальная цена</td><td>{price}</td></tr>
        <tr><td class="label">Величина повышения начальной цены, руб.</td><td>163 700.00</td></tr>
        <tr><td class="label">Размер задатка, руб.</td><td>{deposit}</td></tr>
        <tr><td class="label">Статус торгов</td><td>Торги объявлены</td></tr>
      </tbody>
    </table>
    """


def _document(*cases: tuple[int, str, str, str, str]) -> str:
    return (
        "<html><body><h1 class='node_header'>ООО Асмато "
        "<span>Идентификационный номер</span> 72113-ОАОФ</h1>"
        + "".join(_section(case) for case in cases)
        + "</body></html>"
    )


def test_shared_trade_has_ten_distinct_lot_ids_and_separate_prices():
    parsed = parse_nistp_shared_trade_card(_document(*CASES), trade_nid="502957")
    assert set(parsed) == set(range(1, 11))
    assert len({v.external_id for v in parsed.values()}) == 10
    assert all(v.trade_code == "72113-ОАОФ" for v in parsed.values())
    assert parsed[4].external_id == "nistp.ru:502957:4"
    assert parsed[4].native_lot_nid == "502967"
    assert parsed[5].native_lot_nid == "502969"
    assert parsed[4].address == "г. Москва"
    assert parsed[5].external_id == "nistp.ru:502957:5"
    assert parsed[4].cadastral_number == "77:01:0004002:6614"
    assert parsed[5].cadastral_number == "77:01:0004002:6633"
    assert parsed[4].area_sqm == 14.3
    assert parsed[5].area_sqm == 13.3
    assert parsed[4].initial_price_rub == parsed[5].initial_price_rub == Decimal("3274000")
    assert parsed[4].deposit_rub == parsed[5].deposit_rub == Decimal("327400")
    assert parsed[4].initial_price_rub != parsed[4].deposit_rub


def test_get_requested_number_does_not_return_other_lot_from_shared_url():
    data = _document(*CASES[3:5])
    fourth = get_nistp_lot(data, trade_nid="502957", lot_number=4)
    fifth = get_nistp_lot(data, trade_nid="502957", lot_number=5)
    assert fourth.external_id != fifth.external_id
    assert fourth.cadastral_number != fifth.cadastral_number
    assert fourth.description != fifth.description
    with pytest.raises(NistpCardError, match="requested_lot_missing"):
        get_nistp_lot(data, trade_nid="502957", lot_number=2)


def test_duplicate_section_number_fails_closed():
    data = _document(CASES[3], CASES[3])
    with pytest.raises(NistpCardError, match="duplicate_lot_section"):
        parse_nistp_shared_trade_card(data, trade_nid="502957")


def test_missing_per_lot_price_never_falls_back_to_another_lot_or_deposit():
    data = _document(*CASES[3:5])
    data = data.replace(
        "<td>Начальная цена</td><td>3 274 000.00</td>",
        "<td>Начальная цена</td><td>не указана</td>",
        1,
    )
    with pytest.raises(NistpCardError, match="incomplete_numbered_lot_section"):
        parse_nistp_shared_trade_card(data, trade_nid="502957")


def test_invalid_trade_identifier_or_unproven_card_fails_closed():
    with pytest.raises(NistpCardError, match="invalid_trade_nid"):
        parse_nistp_shared_trade_card(_document(CASES[3]), trade_nid="invalid")
    with pytest.raises(NistpCardError, match="missing_trade_code"):
        parse_nistp_shared_trade_card(
            _document(CASES[3]).replace("72113-ОАОФ", "unknown"),
            trade_nid="502957",
        )


def test_script_with_fake_lot_is_ignored():
    data = _document(CASES[3]).replace(
        "</body>",
        "<script>Лот № 999 Номер лота 999 Начальная цена 1.00</script></body>",
    )
    parsed = parse_nistp_shared_trade_card(data, trade_nid="502957")
    assert list(parsed) == [4]


def test_provider_lot_number_has_to_match_table_and_heading():
    sample = _document(CASES[3])
    wrong = sample.replace('id="table_lot_4"', 'id="table_lot_5"')
    with pytest.raises(NistpCardError, match="lot_heading_does_not_match_table"):
        parse_nistp_shared_trade_card(wrong, trade_nid="502957")
    wrong_field = sample.replace(
        '<td class="label">Номер лота</td><td>4</td>',
        '<td class="label">Номер лота</td><td>5</td>',
    )
    with pytest.raises(NistpCardError, match="lot_number_field_mismatch"):
        parse_nistp_shared_trade_card(wrong_field, trade_nid="502957")


def test_native_lot_id_cannot_repeat_across_two_lots():
    sample = _document(*CASES[3:5])
    wrong = sample.replace('name="lot502969"', 'name="lot502967"')
    with pytest.raises(NistpCardError, match="duplicate_native_lot_identifier"):
        parse_nistp_shared_trade_card(wrong, trade_nid="502957")


def test_price_is_not_taken_from_deposit_or_procedure_table():
    sample = _document(CASES[3])
    sample = sample.replace(
        '<table class="data" id="table_lot_4">',
        '<table class="data" id="procedure"><tr><td class="label">'
        'Начальная цена</td><td>99999999.00</td></tr></table>'
        '<table class="data" id="table_lot_4">',
    )
    parsed = get_nistp_lot(sample, trade_nid="502957", lot_number=4)
    assert parsed.initial_price_rub == Decimal("3274000.00")
    assert parsed.deposit_rub == Decimal("327400.00")


def test_lot_price_cannot_be_replaced_by_arbitrary_text():
    sample = _document(CASES[3]).replace(
        '<td class="label">Начальная цена</td><td>3 274 000.00</td>',
        '<td class="label">Начальная цена</td><td>не указана</td>',
    )
    with pytest.raises(NistpCardError, match="invalid_individual_lot_money"):
        parse_nistp_shared_trade_card(sample, trade_nid="502957")
