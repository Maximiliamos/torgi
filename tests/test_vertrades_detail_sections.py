"""Sanitized regression fixtures for Vertrades shared procedure cards."""
from __future__ import annotations

from decimal import Decimal
import pytest

from bankrotai.connectors.etp_sources.vertrades_detail_sections import (
    VertradesCardError,
    get_vertrades_lot,
)


def _lot(number: int, *, title: str, price: str, status: str = "Торги завершены") -> str:
    return f"""
    <h3>Сведения по лоту №{number}</h3>
    <table>
      <tr><td>Статус торгов по лоту</td><td>{status}</td></tr>
      <tr><td>Краткие сведения об имуществе (наименование лота)</td><td>{title}</td></tr>
      <tr><td>Cведения об арестованном имуществе, реализуемом на торгах</td>
          <td>ЛОТ№{number} {title}. Адрес: г. Волгоград, ул. Тестовая, д.{number}.
          Начальная цена: {price} руб. Задаток: 50 000 руб. Ограничения: запрет регистрации.</td></tr>
      <tr><td>Валюта</td><td>RUB</td></tr>
      <tr><td>Начальная цена продажи имущества</td><td>{price} НДС не облагается</td></tr>
      <tr><td>Величина повышения начальной цены</td><td>20 000,00 (2.00%)</td></tr>
      <tr><td>Размер задатка</td><td>50 000,00</td></tr>
    </table>
    """


def _doc(*lots: str) -> str:
    return """
    <html><body>
    <h3>идентификационный номер: 55-АП</h3>
    <table>
      <tr><td>Начало предоставления заявок на участие</td><td>11.08.2021 08:00</td></tr>
      <tr><td>Окончание предоставления заявок на участие</td><td>06.09.2021 23:59</td></tr>
      <tr><td>Начало подачи предложений о цене имущества</td><td>10.09.2021 10:00</td></tr>
    </table>
    """ + "".join(lots) + "</body></html>"


PROPERTY_1 = "жилое здание пл. 29,6 кв.м. кад.№ 34:02:010003:2648"
PROPERTY_2 = "нежилое здание пл. 9 кв.м. кад.№ 34:34:080147:2677"


def test_exact_section_has_own_price_status_and_property_fields():
    html = _doc(
        _lot(1, title=PROPERTY_1, price="1 060 000,00"),
        _lot(2, title=PROPERTY_2, price="246 800,00", status="Торги по лоту отменены"),
    )
    first = get_vertrades_lot(html, provider_id="102259678", lot_number=1)
    second = get_vertrades_lot(html, provider_id="102259678", lot_number=2)
    assert first.external_id == "vertrades.ru:102259678:1"
    assert second.external_id == "vertrades.ru:102259678:2"
    assert first.procedure_number == second.procedure_number == "55-АП"
    assert first.initial_price_rub == Decimal("1060000.00")
    assert second.initial_price_rub == Decimal("246800.00")
    assert first.initial_price_rub != second.initial_price_rub
    assert first.status_text == "Торги завершены"
    assert second.status_text == "Торги по лоту отменены"
    assert first.cadastral_number == "34:02:010003:2648"
    assert second.cadastral_number == "34:34:080147:2677"
    assert first.area_sqm == 29.6
    assert first.address == "г. Волгоград, ул. Тестовая, д.1."
    assert first.deposit_rub == Decimal("50000.00")
    assert first.application_deadline.isoformat() == "2021-09-06T23:59:00"
    assert first.auction_at.isoformat() == "2021-09-10T10:00:00"


def test_missing_target_price_never_uses_deposit_or_neighbor_price():
    first = _lot(1, title=PROPERTY_1, price="1 060 000,00")
    broken = _lot(2, title=PROPERTY_2, price="246 800,00").replace(
        "<td>Начальная цена продажи имущества</td><td>246 800,00 НДС не облагается</td>",
        "<td>Другая сумма</td><td>246 800,00 НДС не облагается</td>",
    )
    with pytest.raises(VertradesCardError, match="missing_individual_lot_field"):
        get_vertrades_lot(_doc(first, broken), provider_id="102259678", lot_number=2)


def test_requested_lot_must_exist_exactly_once():
    html = _doc(_lot(1, title=PROPERTY_1, price="1 060 000,00"))
    with pytest.raises(VertradesCardError, match="requested_lot_missing"):
        get_vertrades_lot(html, provider_id="102259678", lot_number=2)
    duplicate = _doc(
        _lot(1, title=PROPERTY_1, price="1 060 000,00"),
        _lot(1, title=PROPERTY_1, price="1 060 000,00"),
    )
    with pytest.raises(VertradesCardError, match="duplicate_requested"):
        get_vertrades_lot(duplicate, provider_id="102259678", lot_number=1)


def test_script_fake_section_is_ignored_and_bad_provider_id_rejected():
    html = _doc(_lot(1, title=PROPERTY_1, price="1 060 000,00")).replace(
        "</body>", "<script>Сведения по лоту №99 Начальная цена продажи имущества 1,00</script></body>"
    )
    assert get_vertrades_lot(html, provider_id="102259678", lot_number=1).lot_number == 1
    with pytest.raises(VertradesCardError, match="invalid_provider_id"):
        get_vertrades_lot(html, provider_id="bad", lot_number=1)


def test_ambiguous_target_price_fails_closed():
    lot = _lot(1, title=PROPERTY_1, price="1 060 000,00").replace(
        "</table>",
        "<tr><td>Начальная цена продажи имущества</td><td>999 000,00</td></tr></table>",
    )
    with pytest.raises(VertradesCardError, match="ambiguous_individual_lot_field"):
        get_vertrades_lot(_doc(lot), provider_id="102259678", lot_number=1)
