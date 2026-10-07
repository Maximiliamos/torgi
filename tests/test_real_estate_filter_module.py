"""Regression coverage for the BAT-308 scraper filtering extraction."""

from types import SimpleNamespace

from bankrotai import scrapers
from bankrotai.services import real_estate_filter


def lot(*, category="land", title="Земельный участок", description="", auction_type=None, raw_data=None):
    return SimpleNamespace(
        category=category,
        title=title,
        description=description,
        address=None,
        auction_type=auction_type,
        raw_data=raw_data or {},
    )


def test_scraper_keeps_legacy_function_identity() -> None:
    assert scrapers.is_real_estate_lot is real_estate_filter.is_real_estate_lot
    assert scrapers.is_sale_real_estate_lot is real_estate_filter.is_sale_real_estate_lot
    assert scrapers.has_disallowed_real_estate_terms is real_estate_filter.has_disallowed_real_estate_terms


def test_sale_filters_preserve_real_estate_and_rental_rules() -> None:
    assert scrapers.is_sale_real_estate_lot(lot())
    assert not scrapers.is_sale_real_estate_lot(lot(auction_type="аренда"))
    assert not scrapers.is_sale_real_estate_lot(lot(title="Доля земельного участка"))
    assert not scrapers.is_sale_real_estate_lot(lot(title="Памятник архитектуры"))
    assert not scrapers.is_real_estate_lot(lot(category="vehicle", title="Автомобиль"))


def test_filter_falls_back_to_source_category_metadata() -> None:
    assert scrapers.is_real_estate_lot(lot(category="other", title="лот", raw_data={"categories": ["Земельный участок"]}))
    assert not scrapers.is_real_estate_lot(lot(category="other", title="автомобиль", raw_data={}))
