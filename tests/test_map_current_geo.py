from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotGeoSnapshot, ProcessedLot
from bankrotai.services.map_view import build_map_lots_response


def test_viewport_hot_path_uses_current_geo_not_snapshot_history() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        lot = ProcessedLot(
            external_id="current-geo-map",
            source="test",
            source_system="test",
            title="Current GEO lot",
            description="",
            category="land",
            auction_status="active",
            current_geo_lat=57.6261,
            current_geo_lon=39.8845,
            current_geo_source="photon",
            current_geo_confidence="high",
        )
        session.add(lot)
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="historical",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=10.0,
                centroid_lon=20.0,
            )
        )
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug=None,
            west=39.0,
            south=57.0,
            east=41.0,
            north=59.0,
            limit=10,
            defer_statistics=True,
        )

    assert response["returned"] == 1
    assert response["items"][0]["lat"] == 57.6261
    assert response["items"][0]["lon"] == 39.8845


def test_viewport_hot_path_hides_lot_without_current_geo_even_with_history() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        lot = ProcessedLot(
            external_id="stale-geo-map",
            source="test",
            source_system="test",
            title="Stale GEO lot",
            description="",
            category="land",
            auction_status="active",
            needs_geo_check=True,
        )
        session.add(lot)
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=lot.id,
                geo_source="historical",
                geo_method="fixture",
                geo_confidence="high",
                centroid_lat=57.6261,
                centroid_lon=39.8845,
            )
        )
        session.commit()

        response = build_map_lots_response(
            session,
            city_slug=None,
            west=39.0,
            south=57.0,
            east=41.0,
            north=59.0,
            limit=10,
            defer_statistics=True,
        )

    assert response["returned"] == 0
