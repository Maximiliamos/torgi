from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.services.map_view import (
    build_map_lot_detail,
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
            external_id="primary", source="test", source_system="tbankrot.ru",
            title="Основная публикация", description="", category="land",
            current_price=Decimal("1000000"), auction_status="active",
        )
        session.add(primary)
        session.flush()
        duplicate = ProcessedLot(
            external_id="duplicate", source="test", source_system="torgi.gov.ru",
            title="Публикация ГИС Торги", description="", category="land",
            current_price=Decimal("1100000"), auction_status="active", duplicate_of_id=primary.id,
        )
        session.add(duplicate)
        session.flush()
        canonical = CanonicalLot(canonical_key="map-group", legacy_processed_lot_id=primary.id, title=primary.title, category="land")
        session.add(canonical)
        session.flush()
        session.add_all([
            SourceLot(canonical_lot_id=canonical.id, processed_lot_id=primary.id, source_system="tbankrot.ru", external_id="primary", source_url="https://example.test/tbankrot"),
            SourceLot(canonical_lot_id=canonical.id, processed_lot_id=duplicate.id, source_system="torgi.gov.ru", external_id="duplicate", source_url="https://example.test/gis"),
            LotGeoSnapshot(lot_id=primary.id, geo_source="test", geo_method="fixture", geo_confidence="high", centroid_lat=57.6, centroid_lon=39.8),
        ])
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
