from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun, ProcessedLot, utc_now
from bankrotai.services.quality import list_source_health
from bankrotai.services.reliability import (
    _canonical_source,
    _current_error,
    _current_quality,
    _source_status,
)


def test_reliability_source_aliases_are_canonicalized() -> None:
    assert _canonical_source("ГИС Торги") == "torgi.gov.ru"
    assert _canonical_source("TBankrot") == "tbankrot.ru"
    assert _canonical_source("РАД / ЛОТ-ОНЛАЙН") == "lot-online.ru"


def test_historical_error_is_not_current_after_later_success() -> None:
    now = utc_now()
    item = SimpleNamespace(
        last_error="old timeout",
        last_failure_at=now - timedelta(hours=2),
        last_success_at=now - timedelta(hours=1),
        status="healthy",
        circuit_state="closed",
        freshness_status="fresh",
        coverage_status="fresh",
    )
    assert _current_error(item) is None
    assert _source_status(item) == "healthy"


def test_failure_after_success_is_current() -> None:
    now = utc_now()
    item = SimpleNamespace(
        last_error="new timeout",
        last_failure_at=now,
        last_success_at=now - timedelta(hours=1),
        status="failed",
        circuit_state="closed",
        freshness_status="stale",
        coverage_status="fresh",
    )
    assert _current_error(item) == "new timeout"
    assert _source_status(item) == "failed"


def test_current_quality_excludes_archived_and_ended_lots() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                ProcessedLot(
                    external_id="live",
                    source="test",
                    source_system="test",
                    title="Live",
                    description="",
                    category="land",
                    auction_status="active",
                    address=None,
                    cadastral_number=None,
                ),
                ProcessedLot(
                    external_id="archived",
                    source="test",
                    source_system="test",
                    title="Archived",
                    description="",
                    category="land",
                    auction_status="active",
                    is_archived=True,
                    address=None,
                ),
                ProcessedLot(
                    external_id="closed",
                    source="test",
                    source_system="test",
                    title="Closed",
                    description="",
                    category="land",
                    auction_status="closed",
                    address=None,
                ),
            ]
        )
        session.commit()
        quality = _current_quality(session)

    assert quality["population"] == 1
    assert quality["missing_address"] == 1
    assert quality["missing_cadastre"] == 1
    assert quality["percentages"]["missing_address"] == 100.0


def test_source_health_does_not_expose_old_error_after_newer_success() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = utc_now()
    with Session(engine) as session:
        session.add_all(
            [
                LotSyncRun(
                    id="failure",
                    trigger_type="test",
                    status="failed",
                    total_sources=1,
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2),
                ),
                LotSyncRun(
                    id="success",
                    trigger_type="test",
                    status="success",
                    total_sources=1,
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.add_all(
            [
                LotSyncSourceRun(
                    sync_run_id="failure",
                    source_system="example.ru",
                    status="failed",
                    error_message="old timeout",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2),
                ),
                LotSyncSourceRun(
                    sync_run_id="success",
                    source_system="example.ru",
                    status="success",
                    complete_source_run=True,
                    items_seen=12,
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.commit()
        item = list_source_health(session, now=now)[0]

    assert item.status == "healthy"
    assert item.last_error is None
    assert item.last_error_category is None
