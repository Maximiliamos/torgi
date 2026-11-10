"""Regression tests from NISTP's shared 10-lot trade evidence, 05 Nov 2026.

The synthetic fixture mirrors the verified provider table_lot_N DOM from
the user-supplied HTML (2026-11-05), without contacts or credentials.
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
        '<td class="label">Начальная цена</td><td>3 274 000.00</td>',
        '<td class="label">Начальная цена</td><td>не указана</td>',
        1,
    )
    with pytest.raises(NistpCardError, match="invalid_individual_lot_money"):
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


def test_real_dom_has_earlier_agent_popup_h1_before_procedure_heading():
    html = _document(CASES[3]).replace(
        "<html><body>",
        "<html><body><h1 class='node_header'>Купить лот через агента</h1>",
    )
    details = get_nistp_lot(html, trade_nid="502957", lot_number=4)
    assert details.trade_code == "72113-ОАОФ"
    assert details.native_lot_nid == "502967"


def test_lot_table_does_not_accept_wrong_or_missing_native_anchor():
    html = _document(CASES[3])
    no_lot_anchor = html.replace('<a name="lot4"></a>', "")
    with pytest.raises(NistpCardError, match="lot_number_anchor_missing"):
        parse_nistp_shared_trade_card(no_lot_anchor, trade_nid="502957")
    two_native_ids = html.replace(
        '<a name="lot502967"></a>',
        '<a name="lot502967"></a><a name="lot999999"></a>',
    )
    with pytest.raises(NistpCardError, match="multiple_native_lot_identifiers"):
        parse_nistp_shared_trade_card(two_native_ids, trade_nid="502957")


def test_targeted_lot_ignores_unrelated_incomplete_fields_but_not_duplicate_number():
    """Real 494080:3 had correct own fields but bad other numbered sections."""
    valid = CASES[2]  # numbered lot 3
    incomplete = _section(CASES[0]).replace(
        '<td class="label">Наименование имущества</td>', '<td class="bad">Наименование имущества</td>'
    )
    html = _document(valid).replace("</body>", incomplete + "</body>")
    with pytest.raises(NistpCardError, match="incomplete_numbered_lot_section"):
        parse_nistp_shared_trade_card(html, trade_nid="502957")
    exact = get_nistp_lot(html, trade_nid="502957", lot_number=3)
    assert exact.external_id == "nistp.ru:502957:3"
    assert exact.initial_price_rub == Decimal("3135000.00")
    assert exact.cadastral_number == "77:01:0004002:6699"
    assert exact.native_lot_nid == "502966"
    with pytest.raises(NistpCardError, match="duplicate_lot_section_number"):
        get_nistp_lot(
            html.replace("</body>", _section(valid) + "</body>"),
            trade_nid="502957", lot_number=3,
        )


def test_targeted_lot_remains_fail_closed_on_missing_139_and_forged_price():
    truncated = _document(CASES[0], CASES[1])
    with pytest.raises(NistpCardError, match="requested_lot_missing_in_trade_card"):
        get_nistp_lot(truncated, trade_nid="502379", lot_number=139)
    missing_target_price = _document(CASES[2]).replace(
        '<td class="label">Начальная цена</td><td>3 135 000.00</td>',
        '<td class="label">Начальная цена</td><td>не указана</td>',
    )
    with pytest.raises(NistpCardError, match="invalid_individual_lot_money"):
        get_nistp_lot(missing_target_price, trade_nid="502957", lot_number=3)


def test_targeted_lot_rejects_cross_section_native_identity_duplication():
    """No neighbouring section may claim the requested lot's native identity."""
    other = _section(CASES[0]).replace(
        'name="lot502964"', 'name="lot502966"',
    )
    html = _document(CASES[2]).replace("</body>", other + "</body>")
    with pytest.raises(NistpCardError, match="duplicate_native_lot_identifier"):
        get_nistp_lot(html, trade_nid="502957", lot_number=3)


def test_nistp_direct_table_rows_without_tbody_still_use_exact_lot_fields():
    """Bounded support for HTML cards with direct TR children."""
    html = _document(CASES[3]).replace("<tbody>", "").replace("</tbody>", "")
    result = get_nistp_lot(html, trade_nid="502957", lot_number=4)
    assert result.external_id == "nistp.ru:502957:4"
    assert result.initial_price_rub == Decimal("3274000.00")
    assert result.native_lot_nid == "502967"


def test_nistp_nested_table_cannot_supply_missing_target_price():
    html = _document(CASES[3])
    inner_table = (
        "<table><tr><td class='label'>Начальная цена</td>"
        "<td>3274000.00</td></tr></table>"
    )
    html = html.replace(
        '<td class="label">Начальная цена</td><td>3 274 000.00</td>',
        '<td class="label">Другая сумма</td><td>' + inner_table + '</td>',
    )
    with pytest.raises(NistpCardError, match="incomplete_numbered_lot_section"):
        get_nistp_lot(html, trade_nid="502957", lot_number=4)


def test_misnumbered_target_table_reports_heading_before_native_anchor_error():
    # A deliberate table_lot_4 -> table_lot_5 tamper leaves "lot4" and
    # the native anchor intact. The primary identity error must be reported
    # consistently in both whole-card and exact-lot parsing modes.
    wrong = _document(CASES[3]).replace(
        'id="table_lot_4"', 'id="table_lot_5"',
    )
    with pytest.raises(NistpCardError, match="lot_heading_does_not_match_table"):
        parse_nistp_shared_trade_card(wrong, trade_nid="502957")
    with pytest.raises(NistpCardError, match="lot_heading_does_not_match_table"):
        get_nistp_lot(wrong, trade_nid="502957", lot_number=5)

def test_asset_address_outweighs_generic_region_in_own_numbered_section():
    # One provider-owned table supplies a vague region field and a precise
    # per-lot description; no address from another lot or contact is consulted.
    html = _document(CASES[0]).replace(
        "Машино-место, площадью 13,3 кв. м, кад. № 77:01:0004002:6638",
        "Квартира, адрес: город Москва, вн.тер.г. муниципальный округ "
        "Молжаниновский, шоссе Ленинградское, дом 228, корпус 4, "
        "квартира 363, кадастровый номер: 77:01:0004002:6638",
    )
    result = get_nistp_lot(html, trade_nid="502957", lot_number=1)
    assert "город Москва" in result.address
    assert "Ленинградское" in result.address
    assert "квартира 363" in result.address
    assert result.cadastral_number == "77:01:0004002:6638"

