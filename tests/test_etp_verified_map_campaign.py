"""Regression tests for exact-source, bounded ETP GEO/map promotion.

All source rows, geocoding and S3 publication are synthetic in these tests.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankrotai.services import etp_verified_map_campaign as campaign


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def scalars(self, _query):
        return self

    def all(self):
        return self.rows


def _row(*, lat=None, lon=None, source="m-ets.ru", external_id="m-ets.ru:12345"):
    return SimpleNamespace(
        id=27, source_system=source, external_id=external_id,
        is_archived=False, duplicate_of_id=None,
        auction_status="active", cadastral_number="50:05:0010203:1234",
        current_geo_lat=lat, current_geo_lon=lon,
    )


@pytest.mark.parametrize("source,ids", [
    ("unknown-etp.ru", ("unknown:123",)),
    ("m-ets.ru", ()),
    ("m-ets.ru", ("m-ets.ru:1", "m-ets.ru:2", "m-ets.ru:3")),
    ("m-ets.ru", ("m-ets.ru:1", "m-ets.ru:1")),
    ("cdtrf.ru", ("bad/id",)),
])
def test_rejects_unapproved_or_unbounded_source_before_db(monkeypatch, source, ids):
    monkeypatch.setattr(campaign, "SessionLocal", lambda: pytest.fail("must not open DB"))
    with pytest.raises(ValueError):
        campaign.source_snapshot(source, ids)


def test_only_exact_source_ids_are_audited(monkeypatch):
    monkeypatch.setattr(campaign, "SessionLocal", lambda: _FakeSession([_row(lat=55.1, lon=37.4)]))
    report = campaign.source_snapshot("m-ets.ru", ("m-ets.ru:12345",))
    assert report["source_records_verified"] == 1
    assert report["with_coordinates"] == 1
    assert report["mapped_lot_ids"] == [27]


def test_missing_or_archived_source_lot_is_not_promoted(monkeypatch):
    monkeypatch.setattr(campaign, "SessionLocal", lambda: _FakeSession([]))
    with pytest.raises(RuntimeError, match="not in PostgreSQL"):
        campaign.source_snapshot("m-ets.ru", ("m-ets.ru:12345",))
    archived = _row()
    archived.is_archived = True
    monkeypatch.setattr(campaign, "SessionLocal", lambda: _FakeSession([archived]))
    with pytest.raises(RuntimeError, match="unverified or archived"):
        campaign.source_snapshot("m-ets.ru", ("m-ets.ru:12345",))


def test_geocoding_never_claims_publication_if_no_valid_coordinate(monkeypatch):
    monkeypatch.setattr(campaign, "SessionLocal", lambda: _FakeSession([_row()]))
    from bankrotai.services import geo_backfill
    seen = []
    def no_match(_session, *, limit, lot_ids, refresh_strategy):
        seen.append((limit, lot_ids, refresh_strategy))
        return {"status": "success", "geocoded": 0}
    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", no_match)
    with pytest.raises(RuntimeError, match="no reliable source-scoped GEO"):
        campaign.geocode("m-ets.ru", ("m-ets.ru:12345",))
    assert seen == [(1, [27], False)]


def test_map_publish_requires_verified_coordinates_before_s3(monkeypatch):
    monkeypatch.setattr(campaign, "SessionLocal", lambda: _FakeSession([_row()]))
    from bankrotai.services import map_builder
    monkeypatch.setattr(
        map_builder, "build_map_dataset",
        lambda _session: pytest.fail("must not publish without source GEO"),
    )
    with pytest.raises(RuntimeError, match="genuine source coordinates"):
        campaign.publish("m-ets.ru", ("m-ets.ru:12345",))
