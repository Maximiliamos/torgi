from datetime import datetime

import pytest

pytest.importorskip("PySide6")

from bankrotai.db import LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.gui import MainWindow


@pytest.mark.parametrize("status,ended", [("active", False), ("closed", True)])
def test_desktop_map_uses_own_dates_and_explicit_lifecycle(status, ended):
    lot = ProcessedLot(id=1, external_id="test:1", source_system="test.ru", source="test",
                       title="Участок", description="", category="land", auction_status=status,
                       region_slug="76", is_archived=False, closed_at=datetime(2020, 1, 1),
                       current_geo_lat=57.0, current_geo_lon=39.0)
    own = SourceLot(source_system="test.ru", external_id="test:1", source_url="https://test.ru/lot/1",
                    application_deadline=datetime(2026, 12, 1, 18), auction_at=datetime(2020, 1, 1, 10),
                    procedure_number="own", raw_data={"image_urls": ["https://test.ru/own.jpg"]})
    other = SourceLot(source_system="other.ru", external_id="other:2", source_url="https://other.ru/lot/2",
                      application_deadline=datetime(2035, 1, 1), auction_at=datetime(2035, 1, 2),
                      procedure_number="other", raw_data={"image_urls": ["https://other.ru/other.jpg"]})
    geo = LotGeoSnapshot(centroid_lat=57.0, centroid_lon=39.0, geo_source="test", geo_confidence="high")
    detail = MainWindow._map_payload(lot, geo, [other, own])
    assert detail["application_deadline"] == "01.12.2026 18:00 МСК"
    assert detail["auction_at"] == "01.01.2020 10:00 МСК"
    assert detail["procedure_number"] == "own"
    assert detail["image_urls"] == ["https://test.ru/own.jpg"]
    assert detail["is_ended"] == ended


def test_desktop_map_refuses_reset_current_geo_despite_historical_snapshot():
    lot = ProcessedLot(id=1, current_geo_lat=None, current_geo_lon=None)
    old = LotGeoSnapshot(centroid_lat=57.0, centroid_lon=39.0)
    with pytest.raises(ValueError, match="invalidated"):
        MainWindow._map_payload(lot, old, [])
