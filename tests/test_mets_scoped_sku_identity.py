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


def _scoped_view(
    *,
    include_sku: bool,
    cadastres: tuple[str, ...],
    extra_lot_container: bool = False,
) -> str:
    """One view: a lot-container and its sibling official cadastral info blocks."""
    panel = (
        '<div class="lot-container"><div class="general-block">'
        '<div class="lot-generalinfoblock whiteblock"><div class="header">'
        + (META if include_sku else "")
        + '</div></div></div>'
        + '<h2 class="lot-title">Квартира в Ярославле</h2>'
        + '<div class="lot-property-summary">Недвижимое имущество: '
        + 'жилое помещение, общая площадь 52 квадратных метра.</div>'
        + '<div class="lot-cost-item price"><div class="value">6 220 123 ₽</div>'
        + '<div class="title">Начальная цена</div></div>'
        + '<div class="lot-cost-item status"><div class="lot-status-name">'
        + 'Торги в стадии приема заявок</div></div>'
        + '</div>'
    )
    if extra_lot_container:
        panel += '<div class="lot-container"><h2>Другая карточка</h2></div>'

    info = "".join(
        '<div class="lot-info-block info-type_1"><div class="lot-info-item">'
        '<div class="value"><div class="kad_num">'
        f'<span class="kn_span">{cad}</span>'
        '</div></div></div></div>'
        for cad in cadastres
    )
    return '<div class="generalview-container">' + panel + info + '</div>'


def _multiview_page(
    primary_cadastres: tuple[str, ...],
    *,
    extra_primary_lot: bool = False,
    primary_noindex: bool = False,
) -> str:
    primary_view = _scoped_view(
        include_sku=True,
        cadastres=primary_cadastres,
        extra_lot_container=extra_primary_lot,
    )
    if primary_noindex:
        primary_view = '<noindex>' + primary_view + '</noindex>'
    recommendations = "".join(
        '<noindex>' + _scoped_view(
            include_sku=False,
            cadastres=(f"77:01:0000001:{100 + index}",),
        ) + '</noindex>'
        for index in range(14)
    )
    return '<html><body>' + primary_view + recommendations + '</body></html>'


def test_main_view_sibling_cadastre_is_verified_not_recommendations() -> None:
    """Simulate the November 12 provider layout with 15 separate views."""
    from bankrotai.connectors.registry.etp_incremental import _property_ready

    html = _multiview_page(("76:23:0000001:123",))
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select(".generalview-container")) == 15
    assert len(soup.select(".lot-container")) == 15
    assert len(soup.select(".kad_num .kn_span")) == 15
    lot = parse_mets_lot(html, URL)

    assert lot.external_id == "244025-5"
    assert lot.cadastral_number == "76:23:0000001:123"
    assert lot.raw_data["cadastral_numbers"] == ["76:23:0000001:123"]
    assert lot.start_price == 6_220_123
    assert lot.auction_status == "active"
    assert _property_ready(lot) is True


def test_rec_only_cadastres_are_never_borrowed_by_primary_view() -> None:
    html = _multiview_page(())
    lot = parse_mets_lot(html, URL)
    assert lot.cadastral_number is None
    assert lot.raw_data["cadastral_numbers"] == []


def test_ambiguous_primary_view_cannot_supply_sibling_cadastre() -> None:
    html = _multiview_page(
        ("76:23:0000001:123",),
        extra_primary_lot=True,
    )
    lot = parse_mets_lot(html, URL)
    assert lot.cadastral_number is None


def test_noindex_primary_view_cannot_supply_sibling_cadastre() -> None:
    html = _multiview_page(
        ("76:23:0000001:123",),
        primary_noindex=True,
    )
    lot = parse_mets_lot(html, URL)
    assert lot.cadastral_number is None


def test_main_view_multiple_cadastres_preserved_as_multi_object() -> None:
    html = _multiview_page((
        "76:23:0000001:123",
        "76:23:0000001:124",
        "76:23:0000001:123",
    ))
    lot = parse_mets_lot(html, URL)
    assert lot.cadastral_number == "76:23:0000001:123"
    assert lot.raw_data["cadastral_numbers"] == [
        "76:23:0000001:123", "76:23:0000001:124",
    ]
    assert lot.raw_data["cadastral_multi_object"] is True


def test_unscoped_page_cannot_borrow_unrelated_generalview_cadastre() -> None:
    html = (
        '<html><body>'
        '<div>Идентификационный номер: 244025-МЭТС-5</div>'
        '<h2>Квартира в Ярославле</h2>'
        '<div>6 220 123 ₽ Начальная цена</div>'
        '<div>Торги в стадии приема заявок</div>'
        + _scoped_view(include_sku=False, cadastres=("77:01:0000001:888",))
        + '</body></html>'
    )
    lot = parse_mets_lot(html, URL)
    assert lot.cadastral_number is None

