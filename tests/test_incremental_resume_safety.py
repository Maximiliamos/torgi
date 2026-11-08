from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.connectors.registry.cdtrf_sync import CdtrfSourceCrawler
from bankrotai.connectors.registry.mets_sync import MetsSyncFilters
from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun, utc_now
from bankrotai.services.ingestion_incremental import (
    filter_signature,
    resume_checkpoint_for_source,
)
from bankrotai.services.ingestion_models import SourceSyncSpec


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _spec() -> SourceSyncSpec:
    return SourceSyncSpec(
        "m-ets.ru",
        MetsSyncFilters(),
        reconcile_missing=False,
        max_batches=500,
        require_source_end=True,
        reuse_unchanged_details=True,
    )


def _seed_previous(sessions, *, error: str, signature: str, age_hours: int = 1) -> None:
    now = utc_now()
    with sessions.begin() as session:
        session.add(
            LotSyncRun(
                id="previous-run",
                trigger_type="scheduled_incremental",
                status="failed",
                total_sources=7,
                started_at=now - timedelta(hours=age_hours, minutes=5),
                finished_at=now - timedelta(hours=age_hours),
            )
        )
        session.add(
            LotSyncSourceRun(
                sync_run_id="previous-run",
                source_system="m-ets.ru",
                status="failed",
                complete_source_run=False,
                pages_scanned=11,
                items_seen=1000,
                items_inserted=4,
                items_updated=6,
                items_unchanged=990,
                items_failed=0,
                error_message=error,
                checkpoint_json={
                    "filter_signature": signature,
                    "resume_cursor": "12:" + "a" * 64,
                    "connector_checkpoint": {
                        "version": 1,
                        "source": "m-ets.ru",
                        "next_cursor": "12:" + "a" * 64,
                    },
                    "detail_attempts": 55,
                    "detail_skipped_unchanged": 945,
                    "retry_count": 2,
                },
                started_at=now - timedelta(hours=age_hours, minutes=5),
                finished_at=now - timedelta(hours=age_hours),
            )
        )


def test_resume_accepts_recent_matching_transport_failure() -> None:
    sessions = _sessions()
    signature = filter_signature(_spec())
    _seed_previous(sessions, error="ReadTimeout: read timed out", signature=signature)
    resume = resume_checkpoint_for_source(
        sessions, "new-run", "m-ets.ru", signature
    )
    assert resume is not None
    assert resume["resumed_from_run_id"] == "previous-run"
    assert resume["pages_scanned"] == 11
    assert resume["detail_attempts"] == 55


def test_resume_rejects_parser_or_validation_failure() -> None:
    sessions = _sessions()
    signature = filter_signature(_spec())
    _seed_previous(
        sessions,
        error="M-ETS systemic detail verification failure: zero verified property lots",
        signature=signature,
    )
    assert resume_checkpoint_for_source(
        sessions, "new-run", "m-ets.ru", signature
    ) is None


def test_resume_rejects_filter_change_and_stale_checkpoint() -> None:
    sessions = _sessions()
    signature = filter_signature(_spec())
    _seed_previous(
        sessions,
        error="ConnectionError: failed to connect",
        signature="different-filter-signature",
    )
    assert resume_checkpoint_for_source(
        sessions, "new-run", "m-ets.ru", signature
    ) is None

    stale = _sessions()
    _seed_previous(
        stale,
        error="ConnectionError: failed to connect",
        signature=signature,
        age_hours=25,
    )
    assert resume_checkpoint_for_source(
        stale, "new-run", "m-ets.ru", signature
    ) is None


def test_cdtrf_fingerprint_tracks_application_window() -> None:
    hit = {
        "external_id": "cdtrf:101",
        "trade_id": "201",
        "trade_lot_id": "101",
        "listing_price_rub": 100,
        "source_status_text": "Прием заявок",
        "request_time_begin": "2026-11-09T10:00:00",
        "request_time_end": "2026-12-27T18:00:00",
        "region_id": 76,
        "property_category_ids": [41],
    }
    changed = dict(hit)
    changed["request_time_end"] = "2026-12-28T18:00:00"
    assert (
        CdtrfSourceCrawler._catalogue_fingerprint(hit)
        != CdtrfSourceCrawler._catalogue_fingerprint(changed)
    )
