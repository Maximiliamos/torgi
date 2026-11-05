"""Never assign a shared procedure row's price/deposit to two different lots."""
from __future__ import annotations

from bankrotai.connectors.etp_sources import PROFILES, parse_listing_html


def _profile(source: str):
    return next(p for p in PROFILES if p.source_id == source)


def test_two_lots_in_same_row_get_only_anchor_specific_fields():
    html = """
    <table><tr><td>
      <a href='https://bankrupt.etpu.ru/public/auctions/lots/view/224/'>
        Земельный участок 76:12:0000123:456</a>
      <a href='https://bankrupt.etpu.ru/public/auctions/lots/view/225/'>
        Квартира 77:01:0001010:1234</a>
    </td><td>
      Начальная цена 900 000 руб. Размер задатка 90 000 руб.
      Прием заявок. Общие условия процедуры.
    </td></tr></table>
    """
    lots = parse_listing_html(_profile("etpu.ru"), html)
    assert len(lots) == 2
    assert {lot.external_id for lot in lots} == {
        "etpu.ru:auctions-224", "etpu.ru:auctions-225",
    }
    assert {lot.cadastral_number for lot in lots} == {
        "76:12:0000123:456", "77:01:0001010:1234",
    }
    assert all(lot.start_price is None and lot.current_price is None for lot in lots)
    assert all(lot.auction_status == "unknown" for lot in lots)
    assert all(lot.raw_data["ambiguous_multi_lot_row"] is True for lot in lots)
    assert all(lot.raw_data["individual_lot_identity_verified"] is False for lot in lots)
    assert lots[0].title != lots[1].title
    assert lots[0].description != lots[1].description


def test_two_links_to_same_lot_are_not_a_multi_lot_row():
    html = """
    <table><tr><td>
      <a href='https://bankrupt.etpu.ru/public/auctions/lots/view/224/'>
        Земельный участок 76:12:0000123:456</a>
      <a href='https://bankrupt.etpu.ru/public/auctions/lots/view/224/'>
        Подробная карточка того же участка</a>
    </td><td>Начальная цена 900 000 руб.</td></tr></table>
    """
    lots = parse_listing_html(_profile("etpu.ru"), html)
    assert len(lots) == 1
    assert lots[0].start_price == 900000
    assert lots[0].raw_data["ambiguous_multi_lot_row"] is False


def test_two_separate_table_rows_keep_proven_row_prices():
    html = """
    <table>
      <tr><td><a href='https://bankrupt.etpu.ru/public/auctions/lots/view/224/'>
        Земельный участок 76:12:0000123:456</a></td>
        <td>Начальная цена 900 000 руб.</td></tr>
      <tr><td><a href='https://bankrupt.etpu.ru/public/auctions/lots/view/225/'>
        Квартира 77:01:0001010:1234</a></td>
        <td>Начальная цена 1 100 000 руб.</td></tr>
    </table>
    """
    lots = parse_listing_html(_profile("etpu.ru"), html)
    assert len(lots) == 2
    assert [lot.start_price for lot in lots] == [900000, 1100000]
    assert all(not lot.raw_data["ambiguous_multi_lot_row"] for lot in lots)
