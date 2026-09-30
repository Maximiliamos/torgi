from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.quality import operational_quality_report


def test_operational_quality_report_groups_sources_photos_and_stale_ids() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        processed = ProcessedLot(
            external_id="one", source="test", source_system="test-source", title="Лот",
            description="", category="land", auction_status="unknown", needs_geo_check=True,
        )
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(canonical_key="one", title="Лот", category="land")
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=processed.id,
            source_system="test-source",
            external_id="one",
            source_status="unknown",
            raw_data={"image_urls": ["https://example.test/one.jpg"]},
            first_seen_at=utc_now() - timedelta(days=20),
            last_seen_at=utc_now() - timedelta(days=10),
        ))
        session.commit()

        report = operational_quality_report(session, stale_days=7)

    assert report["sources"]["test-source"]["total"] == 1
    assert report["sources"]["test-source"]["with_photos"] == 1
    assert report["sources"]["test-source"]["stale_active"] == 1
    assert report["problems"]["stale_active_lots"][0]["external_id"] == "one"
    assert report["lot_data_quality"]["active_non_duplicate_lots"] == 1
    assert report["lot_data_quality"]["active_duplicate_lots"] == 0
    assert report["lot_data_quality"]["missing_title"] == 0
    assert report["lot_data_quality"]["missing_region"] == 1
    assert report["lot_data_quality"]["missing_url"] == 1
    assert report["lot_data_quality"]["missing_price"] == 1
    assert report["lot_data_quality"]["non_positive_price"] == 0
    assert report["lot_data_quality"]["unknown_status"] == 1
    assert report["source_date_quality"]["first_seen_after_last_seen"] == 0
    assert report["source_date_quality"]["application_start_after_deadline"] == 0
    assert report["source_date_quality"]["archived_before_first_seen"] == 0
    assert report["integrity"] == {
        "source_without_processed": 0,
        "recoverable_source_links": 0,
        "processed_without_source": 0,
        "active_and_archived_source_lots": 0,
        "archived_processed_without_timestamp": 0,
        "current_dataset_count": 0,
        "tile_count_mismatches": 0,
    }


def test_operational_quality_report_surfaces_invalid_price_and_dates() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = utc_now()
    with Session(engine) as session:
        processed = ProcessedLot(
            external_id="bad",
            source="test",
            source_system="test-source",
            title=" ",
            description="",
            category="land",
            auction_status="unknown",
            start_price=-1,
        )
        session.add(processed)
        session.flush()
        processed_id = processed.id

        canonical = CanonicalLot(canonical_key="bad", title="Bad", category="land")
        session.add(canonical)
        session.flush()
        source = SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=processed.id,
            source_system="test-source",
            external_id="bad",
            source_status="active",
            first_seen_at=now,
            last_seen_at=now - timedelta(hours=1),
            application_start_at=now,
            application_deadline=now - timedelta(minutes=30),
        )
        session.add(source)
        session.commit()
        source_id = source.id

        report = operational_quality_report(session)

    quality = report["lot_data_quality"]
    assert quality["active_non_duplicate_lots"] == 1
    assert quality["missing_title"] == 1
    assert quality["missing_region"] == 1
    assert quality["missing_url"] == 1
    assert quality["missing_price"] == 0
    assert quality["non_positive_price"] == 1
    assert quality["unknown_status"] == 1
    assert quality["problem_samples"]["missing_title_lot_ids"] == [processed_id]
    assert quality["problem_samples"]["non_positive_price_lot_ids"] == [processed_id]

    dates = report["source_date_quality"]
    assert dates["first_seen_after_last_seen"] == 1
    assert dates["application_start_after_deadline"] == 1
    assert dates["archived_before_first_seen"] == 0
    assert dates["problem_samples"]["first_seen_after_last_seen_source_lot_ids"] == [source_id]
    assert dates["problem_samples"]["application_start_after_deadline_source_lot_ids"] == [source_id]
