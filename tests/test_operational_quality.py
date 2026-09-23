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
            last_seen_at=utc_now() - timedelta(days=10),
        ))
        session.commit()

        report = operational_quality_report(session, stale_days=7)

    assert report["sources"]["test-source"]["total"] == 1
    assert report["sources"]["test-source"]["with_photos"] == 1
    assert report["sources"]["test-source"]["stale_active"] == 1
    assert report["problems"]["stale_active_lots"][0]["external_id"] == "one"
    assert report["integrity"] == {
        "source_without_processed": 0,
        "recoverable_source_links": 0,
        "processed_without_source": 0,
        "active_and_archived_source_lots": 0,
        "archived_processed_without_timestamp": 0,
        "current_dataset_count": 0,
        "tile_count_mismatches": 0,
    }
