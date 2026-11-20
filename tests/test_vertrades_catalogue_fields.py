import pytest
from bs4 import BeautifulSoup
from bankrotai.connectors.etp_sources.vertrades_catalogue_fields import catalogue_asset_fields
from bankrotai.connectors.etp_sources.vertrades import VertradesCardError


@pytest.mark.parametrize("house", ["3", "123"])
@pytest.mark.parametrize("price_node", ["<p class='contests_start'>45 900,00</p>", "45 900,00 руб."])
def test_house_number_cannot_become_part_of_price(house, price_node):
    node = BeautifulSoup(
        f"<p class='contests_lot'><a href='showTrades?id=102286319#lot-19'>"
        f"ЛОТ№19(1112/1164) 1/6 доли жилого здания ул.Центральная д.{house}</a>{price_node}</p>",
        "html.parser",
    ).find("p")
    title, price = catalogue_asset_fields(node)
    assert title.endswith("д." + house)
    assert price == 45900


def test_ambiguous_own_price_nodes_are_refused():
    node = BeautifulSoup("<p><a>Квартира</a><p class='contests_start'>100</p><p class='contests_start'>200</p></p>", "html.parser").find("p")
    with pytest.raises(VertradesCardError, match="ambiguous"):
        catalogue_asset_fields(node)
