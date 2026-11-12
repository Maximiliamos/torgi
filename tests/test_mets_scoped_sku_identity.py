"""Regression: M-ETS machine-readable SKU is valid identity evidence within its lot panel.

Pure offline fixtures. No requests, DB access or source reconciliation.
"""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from bankrotai.connectors.registry.mets_public import MetsEvidenceError, parse_mets_lot


URL = "https://m-ets.ru/244025-5"
SKU = "244025-МЭТС-5"
META = f'<meta itemprop="sku" content="{SKU}">'
VISIBLE = f'<div class="lot-regnumber">Идентификационный номер: {SKU}</div>'


def _page(*, visible_identity: bool = True) -> str:
    # In the observed live layout, the verified SKU belongs to the primary
    # .lot-container, but the readable identifier lives outside that container.
    # Other .lot-container elements are unrelated recommendations.
    recommendations = "".join(
        '<div class="lot-container">'
        f'<h2 class="lot-title">Рекомендация {index}</h2>'
        '<div class="lot-cost-item"><div class="value">9 999 999 ₽</div>'
        '<div class="title">Начальная цена</div></div>'
        '</div>'
        for index in range(14)
    )
    return (
        '<html><body>'
        + (VISIBLE if visible_identity else '')
        + '<div class="lot-container">'
        + META
        + '<h2 class="lot-title">Земельный участок, г. Ярославль</h2>'
        + '<div class="lot-cost-item price">'
        + '<div class="value">6 220 123 ₽</div>'
        + '<div class="title">Начальная цена</div></div>'
        + '<div class="lot-cost-item status">'
        + '<div class="lot-status-name">Торги в стадии приема заявок</div></div>'
        + '<div class="lot-cost-item date">'
        + '<div class="title">Окончание приема заявок:</div>'
        + '<div class="value">20.11.2026 11:00</div></div>'
        + '</div>'
        + recommendations
        + '</body></html>'
    )


@pytest.mark.parametrize("visible_identity", [False, True])
def test_unique_scoped_sku_is_sufficient_without_visible_id(visible_identity: bool) -> None:
    html = _page(visible_identity=visible_identity)
    soup = BeautifulSoup(html, "html.parser")
    sku = soup.select_one('[itemprop="sku"][content]')
    assert sku is not None
    primary = sku.find_parent(class_="lot-container")
    assert primary is not None
    assert SKU not in primary.get_text(" ", strip=True)
    assert len(soup.select(".lot-container")) == 15

    lot = parse_mets_lot(html, URL)

    assert lot.external_id == "244025-5"
    assert lot.title == "Земельный участок, г. Ярославль"
    assert lot.start_price == 6_220_123
    assert lot.auction_status == "active"
    assert lot.application_deadline.isoformat() == "2026-11-20T11:00:00"
    assert "Рекомендация" not in lot.title
    assert lot.lot_url == URL


def test_other_sku_is_never_accepted_even_with_visible_matching_id() -> None:
    html = _page().replace(META, '<meta itemprop="sku" content="244025-МЭТС-6">')
    with pytest.raises(MetsEvidenceError, match="SKU"):
        parse_mets_lot(html, URL)


def test_multiple_identical_skus_are_ambiguous_and_fail_closed() -> None:
    html = _page().replace(META, META + META)
    with pytest.raises(MetsEvidenceError, match="SKU"):
        parse_mets_lot(html, URL)


def test_unscoped_head_sku_still_requires_visible_identifier() -> None:
    html = (
        _page(visible_identity=False)
        .replace(META, "", 1)
        .replace("<html><body>", f"<html><head>{META}</head><body>", 1)
    )
    with pytest.raises(MetsEvidenceError, match="identifier"):
        parse_mets_lot(html, URL)


def test_no_sku_and_no_visible_identifier_fail_closed() -> None:
    html = _page(visible_identity=False).replace(META, "", 1)
    with pytest.raises(MetsEvidenceError, match="identifier"):
        parse_mets_lot(html, URL)
