"""Per-lot detail provenance, monetary integrity and fail-closed regression tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankrotai.connectors.etp_sources.source_specific import apply_detail


def _lot(*, identity="etp-profit.ru:otpp-22740-40662",
         source="etp-profit.ru", start_price=1_000_000.0,
         current_price=None, status="active", detail_level="search"):
    return SimpleNamespace(
        external_id=identity, source_system=source,
        title="Земельный участок", description="Объект недвижимости",
        category="land", address=None, cadastral_number="22:41:040210:35",
        vin=None, area=None, start_price=start_price,
        current_price=current_price, auction_status=status,
        application_deadline=None, auction_at=None,
        procedure_number=None, detail_level=detail_level, raw_data={},
    )


@pytest.mark.parametrize("detail", [
    {"identity": "etp-profit.ru:otpp-22740-40663"},
    {"source": "ausib.ru"},
    {"detail_level": "search"},
])
def test_cross_lot_or_unverified_card_is_rejected_before_mutation(detail):
    listing = _lot()
    other = _lot(**{"detail_level": "detail", **detail})
    with pytest.raises(ValueError, match="etp_detail_"):
        apply_detail(listing, other, version=1)
    assert listing.start_price == 1_000_000
    assert listing.detail_level == "search"
    assert listing.raw_data == {}


@pytest.mark.parametrize("listing_price, detail_price", [
    (1_000_000, 100_000),
    (1_000_000, 1_250_000),
])
def test_conflicting_start_price_is_rejected_without_poisoning_listed_lot(
    listing_price, detail_price,
):
    listing = _lot(start_price=listing_price)
    detail = _lot(start_price=detail_price, detail_level="detail")
    with pytest.raises(ValueError, match="start_price_conflicts"):
        apply_detail(listing, detail, version=4)
    assert listing.start_price == listing_price
    assert listing.raw_data == {}


def test_missing_detail_money_does_not_claim_to_have_verified_listing_price():
    listing = _lot()
    detail = _lot(start_price=None, status="unknown", detail_level="detail")
    updated = apply_detail(listing, detail, version=5)
    assert updated.start_price == 1_000_000
    assert updated.auction_status == "active"
    assert updated.raw_data["detail_identity_verified"] is True
    assert updated.raw_data["detail_start_price_verified"] is False
    assert updated.raw_data["listing_price_used_as_fallback"] is True
    assert updated.raw_data["detail_status_verified"] is False


def test_verified_price_and_updated_current_price_use_individual_card():
    listing = _lot(current_price=900_000)
    detail = _lot(start_price=1_000_000, current_price=850_000,
                  status="closed", detail_level="detail")
    updated = apply_detail(listing, detail, version=2)
    assert updated.start_price == 1_000_000
    assert updated.current_price == 850_000
    assert updated.auction_status == "closed"
    assert updated.raw_data["detail_start_price_verified"] is True
    assert updated.raw_data["detail_current_price_verified"] is True
    assert updated.raw_data["detail_status_verified"] is True
    assert updated.raw_data["listing_price_used_as_fallback"] is False


@pytest.mark.parametrize("price", [0, -1, float("inf"), float("nan")])
def test_nonsensical_money_is_rejected_before_enrichment(price):
    listing = _lot()
    detail = _lot(start_price=price, detail_level="detail")
    with pytest.raises(ValueError, match="etp_invalid_money"):
        apply_detail(listing, detail, version=1)
    assert listing.start_price == 1_000_000
    assert listing.detail_level == "search"


def test_verified_detail_preserves_two_decimal_money():
    listing = _lot(start_price=1000000.01)
    detail = _lot(start_price=1000000.01, detail_level="detail")
    updated = apply_detail(listing, detail, version=1)
    assert updated.start_price == 1000000.01
    assert updated.raw_data["detail_start_price_verified"] is True


def test_more_than_two_decimal_places_is_not_accepted_as_verified_rub():
    listing = _lot(start_price=1000000.00)
    detail = _lot(start_price=1000000.009, detail_level="detail")
    with pytest.raises(ValueError, match="etp_invalid_money_precision"):
        apply_detail(listing, detail, version=1)
