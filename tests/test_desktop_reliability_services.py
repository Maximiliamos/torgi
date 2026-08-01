from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import (
    Base,
    CanonicalLot,
    DiagnosticEvent,
    DuplicateReview,
    GeoFailure,
    LotDocumentChange,
    ProcessedLot,
    RawLot,
    SourceLot,
)
from bankrotai.documents import compare_document_versions, record_document_version
from bankrotai.finance import MaxBidInputs
from bankrotai.services.duplicates import manual_merge_lots, manual_split_lot
from bankrotai.services.operations import (
    add_lot_note,
    diagnostic_export,
    save_max_bid_scenario,
    save_participation_checklist,
    save_search,
    toggle_watchlist,
)
from bankrotai.services.quality import (
    apply_raw_payload_retention,
    data_quality_snapshot,
    geo_retry_lot_ids,
    list_source_health,
    record_diagnostic,
    record_geo_failure,
    resolve_geo_failure,
    update_source_health,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _lot(external_id: str, **overrides) -> ProcessedLot:
    values = {
        "external_id": external_id,
        "source": "test",
        "source_system": "test",
        "title": f"Lot {external_id}",
        "description": "Description",
        "category": "land",
        "auction_status": "active",
    }
    values.update(overrides)
    return ProcessedLot(**values)


def _source(session: Session, lot: ProcessedLot, suffix: str) -> SourceLot:
    canonical = CanonicalLot(
        canonical_key=f"canonical-{suffix}",
        legacy_processed_lot_id=lot.id,
        title=lot.title,
        category=lot.category,
    )
    session.add(canonical)
    session.flush()
    source = SourceLot(
        canonical_lot_id=canonical.id,
        processed_lot_id=lot.id,
        source_system=lot.source_system,
        external_id=f"source-{suffix}",
    )
    session.add(source)
    session.flush()
    return source


def test_quality_health_geo_queue_and_retention() -> None:
    with Session(_engine()) as session:
        lot = _lot("quality", needs_geo_check=False)
        session.add(lot)
        session.flush()
        source = _source(session, lot, "quality")
        source.raw_data = {"large": "payload"}
        source.created_at = utc_now() - timedelta(days=60)
        session.add(RawLot(
            source="test",
            external_id="old",
            raw_data={"large": "payload"},
            created_at=utc_now() - timedelta(days=60),
        ))

        update_source_health(session, "test", status="running")
        update_source_health(session, "test", status="healthy", items_seen=12)
        health = list_source_health(session)
        assert health[0].status == "healthy"
        assert health[0].items_seen == 12

        failure = record_geo_failure(session, lot.id, "address not found", retry_after_seconds=0)
        assert failure.attempt_count == 1
        assert geo_retry_lot_ids(session) == [lot.id]
        assert lot.needs_geo_check is True
        assert resolve_geo_failure(session, lot.id) is True
        assert session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot.id)).status == "resolved"
        assert lot.needs_geo_check is False

        result = apply_raw_payload_retention(session, retention_days=30)
        assert result == {"raw_deleted": 1, "source_cleared": 1}
        assert data_quality_snapshot(session).total_lots == 1


def test_manual_merge_and_split_preserve_audit_trail() -> None:
    with Session(_engine()) as session:
        primary, secondary = _lot("primary"), _lot("secondary")
        session.add_all([primary, secondary])
        session.flush()
        primary_source = _source(session, primary, "primary")
        secondary_source = _source(session, secondary, "secondary")
        own_canonical_id = secondary_source.canonical_lot_id

        review = manual_merge_lots(session, primary.id, secondary.id, reason="same cadastre")
        assert review.action == "merge"
        assert secondary.duplicate_of_id == primary.id
        assert secondary_source.canonical_lot_id == primary_source.canonical_lot_id

        split = manual_split_lot(session, secondary.id, reason="different building")
        assert split.action == "split"
        assert secondary.duplicate_of_id is None
        assert secondary_source.canonical_lot_id == own_canonical_id
        assert len(session.scalars(select(DuplicateReview)).all()) == 2


def test_deal_helpers_and_sanitized_diagnostic_export() -> None:
    with Session(_engine()) as session:
        lot = _lot("deal", current_price=1_000_000)
        session.add(lot)
        session.flush()
        _source(session, lot, "deal")

        scenario = save_max_bid_scenario(
            session,
            lot.id,
            MaxBidInputs(conservative_sale_price=2_000_000, intended_bid=1_000_000),
            name="Base case",
        )
        assert scenario.results_json["base"]["expected_profit"] == 1_000_000
        checklist = save_participation_checklist(session, lot.id, {"deposit_sent": True, "notes": "ok"})
        assert checklist.deposit_sent is True
        assert toggle_watchlist(session, lot.id) is True
        assert toggle_watchlist(session, lot.id) is False
        assert add_lot_note(session, lot.id, "Inspect title").content == "Inspect title"
        assert save_search(session, "Yaroslavl land", {"category": "land"}).query_params["category"] == "land"
        record_diagnostic(
            session,
            severity="warning",
            component="test",
            message="retry",
            context={"attempt": 2},
        )
        exported = diagnostic_export(session)
        assert exported["application_version"]
        assert exported["quality"]["total_lots"] == 1
        assert exported["events"][0]["message"] == "retry"


def test_document_versions_are_immutable_and_comparable() -> None:
    with Session(_engine()) as session:
        lot = _lot("document")
        session.add(lot)
        session.flush()
        source = _source(session, lot, "document")
        document, first, created = record_document_version(
            session,
            source_lot_id=source.id,
            external_document_id="notice",
            filename="notice.pdf",
            content=b"version one",
            storage_key="docs/one.pdf",
            mime_type="application/pdf",
            metadata={"pages": 1},
        )
        assert created is True
        _, repeated, created = record_document_version(
            session,
            source_lot_id=source.id,
            external_document_id="notice",
            filename="notice.pdf",
            content=b"version one",
            storage_key="docs/repeated.pdf",
        )
        assert created is False
        assert repeated.id == first.id
        _, second, _ = record_document_version(
            session,
            source_lot_id=source.id,
            external_document_id="notice",
            filename="notice.pdf",
            content=b"version two is longer",
            storage_key="docs/two.pdf",
            mime_type="application/pdf",
            metadata={"pages": 2},
        )
        change = compare_document_versions(session, first.id, second.id)
        assert change.document_id == document.id
        assert change.summary_json["content_changed"] is True
        assert change.summary_json["metadata_changes"]["pages"] == {"before": 1, "after": 2}
        assert session.scalar(select(LotDocumentChange)).id == change.id
        assert compare_document_versions(session, first.id, second.id).id == change.id
        assert session.scalar(select(DiagnosticEvent)) is None
