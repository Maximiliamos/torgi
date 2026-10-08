from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import Base, MapDataset, MapTile, ProcessedLot
from bankrotai.services.quality import map_delivery_reconciliation_report


def _mapped_lot(external_id: str) -> ProcessedLot:
    return ProcessedLot(
        external_id=external_id,
        source="test",
        source_system="test",
        title=external_id,
        description="",
        category="land",
        region_code="76",
        cadastral_number="76:23:010101:1",
        auction_status="active",
        current_geo_lat=57.6261,
        current_geo_lon=39.8845,
        current_geo_source="nspd",
        current_geo_confidence="high",
    )


def test_map_delivery_reconciliation_matches_db_and_dataset() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        lot = _mapped_lot("one")
        session.add(lot)
        session.flush()
        dataset = MapDataset(
            version="20260930T000000000000Z-r7-bundle-s3",
            status="ready",
            is_current=True,
            point_count=1,
            tile_count=1,
            published_at=utc_now().replace(tzinfo=None),
        )
        session.add(dataset)
        session.flush()
        session.add(
            MapTile(
                dataset_id=dataset.id,
                z=12,
                x=2500,
                y=1300,
                feature_count=1,
                etag="a" * 64,
                payload_json={
                    "features": [
                        {
                            "kind": "lot",
                            "id": lot.id,
                            "lat": 57.6261,
                            "lon": 39.8845,
                        }
                    ]
                },
            )
        )
        session.commit()

        report = map_delivery_reconciliation_report(session)

    assert report["ok"] is True
    assert report["db_candidate_count"] == 1
    assert report["db_eligible_count"] == 1
    assert report["dataset_unique_lot_count"] == 1
    assert report["missing_from_dataset_count"] == 0
    assert report["extra_in_dataset_count"] == 0
    assert report["point_count_matches"] is True
    assert report["tile_count_matches"] is True
    assert report["manifest"] == {"checked": False, "ok": None}


def test_map_delivery_reconciliation_exposes_missing_eligible_lot() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        present = _mapped_lot("present")
        missing = _mapped_lot("missing")
        missing.cadastral_number = "76:23:010101:2"
        session.add_all([present, missing])
        session.flush()
        missing_id = missing.id
        dataset = MapDataset(
            version="20260930T000000000000Z-r7-bundle-s3",
            status="ready",
            is_current=True,
            point_count=1,
            tile_count=1,
            published_at=utc_now().replace(tzinfo=None),
        )
        session.add(dataset)
        session.flush()
        session.add(
            MapTile(
                dataset_id=dataset.id,
                z=12,
                x=2500,
                y=1300,
                feature_count=1,
                etag="b" * 64,
                payload_json={"features": [{"kind": "lot", "id": present.id}]},
            )
        )
        session.commit()

        report = map_delivery_reconciliation_report(session)

    assert report["ok"] is False
    assert report["db_eligible_count"] == 2
    assert report["missing_from_dataset_count"] == 1
    assert report["missing_from_dataset_sample_lot_ids"] == [missing_id]



def test_map_delivery_reconciliation_ignores_post_dataset_geo_arrival() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        present = _mapped_lot("present")
        session.add(present)
        session.flush()
        dataset = MapDataset(
            version="20261007T000000000000Z-r7-bundle-s3",
            status="ready",
            is_current=True,
            point_count=1,
            tile_count=1,
            published_at=utc_now().replace(tzinfo=None),
        )
        session.add(dataset)
        session.flush()
        session.add(
            MapTile(
                dataset_id=dataset.id,
                z=12,
                x=2500,
                y=1300,
                feature_count=1,
                etag="c" * 64,
                payload_json={"features": [{"kind": "lot", "id": present.id}]},
            )
        )

        arrived = _mapped_lot("arrived-after-build")
        arrived.cadastral_number = "76:23:010101:3"
        arrived.current_geo_observed_at = dataset.created_at + timedelta(seconds=1)
        arrived.last_update = dataset.created_at + timedelta(seconds=1)
        session.add(arrived)
        session.commit()
        arrived_id = arrived.id

        report = map_delivery_reconciliation_report(session)

    assert report["ok"] is True
    assert report["live_missing_from_dataset_count"] == 1
    assert report["pending_post_dataset_missing_count"] == 1
    assert report["pending_post_dataset_missing_sample_lot_ids"] == [arrived_id]
    assert report["missing_from_dataset_count"] == 0


def test_map_delivery_reconciliation_ignores_post_dataset_removal_until_next_build() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        lot = _mapped_lot("archived-after-build")
        session.add(lot)
        session.flush()
        dataset = MapDataset(
            version="20261007T010000000000Z-r7-bundle-s3",
            status="ready",
            is_current=True,
            point_count=1,
            tile_count=1,
            published_at=utc_now().replace(tzinfo=None),
        )
        session.add(dataset)
        session.flush()
        session.add(
            MapTile(
                dataset_id=dataset.id,
                z=12,
                x=2500,
                y=1300,
                feature_count=1,
                etag="d" * 64,
                payload_json={"features": [{"kind": "lot", "id": lot.id}]},
            )
        )
        session.commit()

        lot.is_archived = True
        lot.archived_at = dataset.created_at + timedelta(seconds=1)
        lot.last_update = dataset.created_at + timedelta(seconds=1)
        session.commit()

        report = map_delivery_reconciliation_report(session)

    assert report["ok"] is True
    assert report["live_extra_in_dataset_count"] == 1
    assert report["pending_post_dataset_extra_count"] == 1
    assert report["extra_in_dataset_count"] == 0
