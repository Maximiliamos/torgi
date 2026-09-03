from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.services.map_view import (
    build_map_lot_detail,
    build_map_lot_statistics,
    build_map_lots_response,
    extract_map_image_urls,
)


def test_map_images_accept_only_public_http_urls_and_remove_duplicates() -> None:
    raw = {
        "photos": [
            {"url": "https://example.test/one.jpg"},
            {"thumbnail": "//example.test/two.jpg"},
            {"image_url": "javascript:alert(1)"},
            {"photo_url": "file:///private/photo.jpg"},
            {"url": "https://example.test/one.jpg"},
        ]
    }

    assert extract_map_image_urls(raw) == [
        "https://example.test/one.jpg",
        "https://example.test/two.jpg",
    ]


def test_map_payload_lists_every_publication_merged_into_primary_lot() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        primary = ProcessedLot(
            external_id="primary",
            source="test",
            source_system="tbankrot.ru",
            title="Основная публикация",
            description="",
            category="land",
            current_price=Decimal("1000000"),
            auction_status="active",
        )
        session.add(primary)
        session.flush()
        duplicate = ProcessedLot(
            external_id="duplicate",
            source="test",
            source_system="torgi.gov.ru",
            title="Публикация ГИС Торги",
            description="",
            category="land",
            current_price=Decimal("1100000"),
            auction_status="active",
            duplicate_of_id=primary.id,
        )
        session.add(duplicate)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="map-group", legacy_processed_lot_id=primary.id, title=primary.title, category="land"
        )
        session.add(canonical)
        session.flush()
        session.add_all(
            [
                SourceLot(
                    canonical_lot_id=canonical.id,
                    processed_lot_id=primary.id,
                    source_system="tbankrot.ru",
                    external_id="primary",
                    source_url="https://example.test/tbankrot",
                    application_deadline=datetime(2026, 8, 23, 19, 30),
                ),
                SourceLot(
                    canonical_lot_id=canonical.id,
                    processed_lot_id=duplicate.id,
                    source_system="torgi.gov.ru",
                    external_id="duplicate",
                    source_url="https://example.test/gis",
                ),
                LotGeoSnapshot(
                    lot_id=primary.id,
                    geo_source="test",
                    geo_method="fixture",
                    geo_confidence="high",
                    centroid_lat=57.6,
                    centroid_lon=39.8,
                ),
            ]
        )
        session.commit()
        primary_id = primary.id
        duplicate_id = duplicate.id

        response = build_map_lots_response(session, city_slug=None, include_archived=False, limit=100)
        detail = build_map_lot_detail(session, primary_id)

    assert response["total"] == 1
    assert detail is not None
    publications = detail["sources"]
    assert [item["processed_lot_id"] for item in publications] == [primary_id, duplicate_id]
    assert [item["is_primary"] for item in publications] == [True, False]
    assert [item["url"] for item in publications] == [
        "https://example.test/tbankrot",
        "https://example.test/gis",
    ]
    assert detail["application_deadline"] == "2026-08-23T19:30:00Z"


@pytest.mark.parametrize(
    ("limit", "expected_returned", "expected_truncated"),
    [(2, 2, True), (3, 3, False), (4, 3, False)],
)
def test_map_payload_reports_viewport_truncation(
    limit: int,
    expected_returned: int,
    expected_truncated: bool,
) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(3):
            lot = ProcessedLot(
                external_id=f"viewport-{index}",
                source="test",
                source_system="test",
                title=f"Лот {index}",
                description="",
                category="land",
                auction_status="active",
            )
            session.add(lot)
            session.flush()
            session.add(
                LotGeoSnapshot(
                    lot_id=lot.id,
                    geo_source="test",
                    geo_method="fixture",
                    geo_confidence="high",
                    centroid_lat=57.6 + index / 100,
                    centroid_lon=39.8 + index / 100,
                )
            )
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug=None,
            include_archived=False,
            limit=limit,
        )

    assert response["returned"] == expected_returned
    assert response["limit"] == limit
    assert response["truncated"] is expected_truncated


def test_map_filters_by_subject_and_start_price_in_database() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fixtures = (
            ("yaroslavl-low", "76", "500000", 57.6, 39.8),
            ("yaroslavl-high", "76", "1500000", 57.7, 39.9),
            ("moscow-low", "77", "700000", 55.7, 37.6),
        )
        for external_id, region_code, start_price, lat, lon in fixtures:
            lot = ProcessedLot(
                external_id=external_id,
                source="test",
                source_system="test",
                title=external_id,
                description="",
                category="real_estate",
                region_code=region_code,
                start_price=Decimal(start_price),
                auction_status="active",
            )
            session.add(lot)
            session.flush()
            session.add(LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="test",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=lat,
                centroid_lon=lon,
            ))
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug=None,
            region_code="76",
            max_start_price=1_000_000,
            include_archived=False,
            limit=100,
        )

    assert response["total"] == 1
    assert [item["title"] for item in response["items"]] == ["yaroslavl-low"]
    assert response["items"][0]["start_price"] == 500000.0
    assert response["items"][0]["region_code"] == "76"


def test_map_response_reuses_precomputed_statistics_across_viewports() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            external_id="cached-statistics",
            source="test",
            source_system="test",
            title="Лот",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(lot)
        session.flush()
        session.add(LotGeoSnapshot(
            lot_id=lot.id,
            geo_source="test",
            geo_method="fixture",
            geo_confidence="high",
            centroid_lat=57.6,
            centroid_lon=39.8,
        ))
        session.commit()

        statistics = build_map_lot_statistics(session, city_slug=None)
        first = build_map_lots_response(
            session,
            city_slug=None,
            west=39,
            south=57,
            east=40,
            north=58,
            statistics=statistics,
        )
        second = build_map_lots_response(
            session,
            city_slug=None,
            west=38,
            south=56,
            east=41,
            north=59,
            statistics=statistics,
        )

    assert statistics["total"] == 1
    assert first["total"] == second["total"] == 1
    assert first["mapped_total"] == second["mapped_total"] == 1
    assert first["items"][0]["id"] == second["items"][0]["id"]


def test_map_response_can_defer_cold_full_dataset_statistics() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            external_id="deferred-statistics",
            source="test",
            source_system="test",
            title="Visible lot",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(lot)
        session.flush()
        session.add(LotGeoSnapshot(
            lot_id=lot.id,
            geo_source="test",
            geo_method="fixture",
            geo_confidence="high",
            centroid_lat=57.6,
            centroid_lon=39.8,
        ))
        second_lot = ProcessedLot(
            external_id="deferred-statistics-2",
            source="test",
            source_system="test",
            title="Second visible lot",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(second_lot)
        session.flush()
        session.add(LotGeoSnapshot(
            lot_id=second_lot.id,
            geo_source="test",
            geo_method="fixture",
            geo_confidence="high",
            centroid_lat=57.7,
            centroid_lon=39.9,
        ))
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug=None,
            limit=250,
            west=20,
            south=45,
            east=60,
            north=70,
            defer_statistics=True,
        )

    assert response["returned"] == 2
    assert response["mapped_total"] == 2
    assert response["statistics_exact"] is False

    with Session(engine) as session:
        truncated = build_map_lots_response(
            session,
            city_slug=None,
            limit=1,
            west=20,
            south=45,
            east=60,
            north=70,
            defer_statistics=True,
        )
    assert truncated["returned"] == 1
    assert truncated["truncated"] is True
    assert truncated["total"] == 2
    assert truncated["without_coordinates"] == 0
