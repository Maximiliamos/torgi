"""Keep the catalogue price separate from digits in the adjacent asset address."""
from __future__ import annotations

from copy import copy
import re
from bs4 import Tag


def catalogue_asset_fields(node: Tag) -> tuple[str, float]:
    from bankrotai.connectors.etp_sources.vertrades import (
        VertradesCardError, _CATALOGUE_LOT_PREFIX, _catalogue_money,
        _catalogue_title_and_price, _clean,
    )

    prices = node.find_all("p", class_="contests_start", recursive=False)
    if len(prices) > 1:
        raise VertradesCardError("ambiguous_div_catalogue_price_nodes")
    if prices:
        price = _catalogue_money(prices[0].get_text(" ", strip=True))
        clone = copy(node)
        for price_node in clone.find_all("p", class_="contests_start", recursive=False):
            price_node.extract()
        title = _clean(_CATALOGUE_LOT_PREFIX.sub("", clone.get_text(" ", strip=True)))
    else:
        anchors = node.find_all("a", recursive=False)
        if not anchors:
            return _catalogue_title_and_price(node.get_text(" ", strip=True))
        price_text = " ".join(
            part.get_text(" ", strip=True) if isinstance(part, Tag) else str(part)
            for part in anchors[-1].next_siblings
        )
        price = _catalogue_money(re.sub(r"\s*(?:руб\.?|рублей|₽)\s*$", "", _clean(price_text), flags=re.I))
        title = _clean(_CATALOGUE_LOT_PREFIX.sub("", anchors[-1].get_text(" ", strip=True)))
    if price is None or not title:
        raise VertradesCardError("missing_or_invalid_catalogue_initial_price")
    return title, price
