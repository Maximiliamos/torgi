from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bankrotai.services.detail_freshness import (
    SOURCE_BUSINESS_FIELDS,
    compare_source_values,
    detail_is_fresh,
)


@pytest.fixture
def cdtrf_projection():
    # Deliberately synthetic; never taken from a live auction.
    row = {key: None for key in SOURCE_BUSINESS_FIELDS}
    row.update({
        "source_system": "cdtrf.ru", "external_id": "test-1",
        "canonical_lot_id": 10, "processed_lot_id": 20,
        "title": "Квартира", "source_status": "active", "is_active": True,
        "is_archived": False, "current_price": Decimal("1000000.00"),
        "start_price": Decimal("1000000.00"),
        "application_deadline": datetime(2026, 12, 1),
        "auction_at": datetime(2026, 12, 5),
        "raw_data": {"listing_fingerprint": "v1", "request_id": "abc"},
    })
    return row


def test_service_metadata_does_not_change_business_projection(cdtrf_projection):
    incoming = dict(cdtrf_projection)
    incoming["raw_data"] = {"listing_fingerprint": "v2", "request_id": "def"}
    incoming["source_updated_at"] = datetime(2026, 11, 8)
    incoming["last_seen_at"] = datetime(2026, 11, 8)
    assert not compare_source_values(SimpleNamespace(**cdtrf_projection), incoming).changed


@pytest.mark.parametrize("field,new_value", [
    ("current_price", Decimal("999999.99")),
    ("start_price", Decimal("950000")),
    ("source_status", "closed"),
    ("is_archived", True),
    ("application_deadline", datetime(2026, 12, 2)),
    ("auction_at", datetime(2026, 12, 6)),
    ("deposit_amount", Decimal("10000")),
    ("notice_number", "new-notice"),
    ("auction_step_amount", Decimal("5000")),
    ("next_interval_price", Decimal("900000")),
    ("next_price_reduction_at", datetime(2026, 12, 7)),
    ("public_offer_schedule", [{"price": 900000}]),
    ("organizer_contact", "changed"),
])
def test_business_change_is_never_skipped(cdtrf_projection, field, new_value):
    incoming = dict(cdtrf_projection)
    incoming[field] = new_value
    result = compare_source_values(SimpleNamespace(**cdtrf_projection), incoming)
    assert result.changed and field in result.changed_fields


def test_cdtrf_timezone_equivalent_deadline_is_not_a_business_change(cdtrf_projection):
    existing = dict(cdtrf_projection)
    existing["application_deadline"] = datetime(2026, 12, 1, 6, 0)
    incoming = dict(cdtrf_projection)
    incoming["application_deadline"] = datetime(
        2026, 12, 1, 9, 0, tzinfo=ZoneInfo("Europe/Moscow")
    )
    assert not compare_source_values(SimpleNamespace(**existing), incoming).changed


def test_missing_projection_fails_closed(cdtrf_projection):
    incoming = dict(cdtrf_projection)
    del incoming["application_deadline"]
    assert "application_deadline:missing_projection" in compare_source_values(
        SimpleNamespace(**cdtrf_projection), incoming
    ).changed_fields


def test_raw_only_photos_change_is_detected(cdtrf_projection):
    incoming = dict(cdtrf_projection)
    incoming["raw_data"] = {"photos": ["new.jpg"]}
    assert compare_source_values(SimpleNamespace(**cdtrf_projection), incoming).changed


def test_detail_freshness_requires_compatible_version_and_unchanged_listing():
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    base = dict(
        fetched_at=now - timedelta(hours=1), now=now,
        max_age=timedelta(hours=3), detail_version=2,
        accepted_versions=frozenset({2}),
        previous_listing_fingerprint="same", listing_fingerprint="same",
    )
    assert detail_is_fresh(**base)
    assert not detail_is_fresh(**{**base, "listing_fingerprint": "new"})
    assert not detail_is_fresh(**{**base, "detail_version": 3})
    assert not detail_is_fresh(**{**base, "fetched_at": None})
    assert not detail_is_fresh(**{**base, "fetched_at": now - timedelta(hours=4)})
    assert not detail_is_fresh(**{**base, "source_updated_at": now})
    assert not detail_is_fresh(**{**base, "previous_listing_fingerprint": None})


def test_detail_freshness_rejects_future_timestamp():
    now = datetime(2026, 11, 8)
    assert not detail_is_fresh(
        fetched_at=now + timedelta(minutes=2), now=now,
        max_age=timedelta(days=1), detail_version="1",
        accepted_versions=frozenset({"1"}),
        previous_listing_fingerprint="a", listing_fingerprint="a",
    )
