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
    _retry_overdue,
    _source_status,
    reliability_summary,
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
    assert item.last_historical_error == "old timeout"
    assert item.last_error_category is None


def test_reliability_summary_reads_nested_operational_integrity(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "bankrotai.services.reliability.reliability_sources",
        lambda session: {
            "generated_at": utc_now().isoformat(),
            "configured_total": 1,
            "registered_total": 1,
            "monitored_total": 1,
            "paused_total": 0,
            "healthy": 1,
            "degraded": 0,
            "failed": 0,
            "not_checked": 0,
            "items": [],
        },
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability.geocoding_progress",
        lambda session: {"paused": False, "percent": 100.0, "geocoded": 1},
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability.operations_host_status",
        lambda session: {
            "backup_healthy": True,
            "runner_healthy": True,
            "disk": {"free_gb": 100.0},
        },
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability._current_quality",
        lambda session: {"population": 1, "percentages": {}},
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability._heavy_quality_bundle",
        lambda session: (
            {
                "integrity": {
                    "source_without_processed": 2,
                    "processed_without_source": 3,
                    "current_dataset_count": 1,
                    "tile_count_mismatches": 0,
                }
            },
            {
                "ok": True,
                "dataset": {
                    "version": "test-s3",
                    "status": "ready",
                    "declared_point_count": 1,
                    "published_at": utc_now(),
                },
                "point_count_matches": True,
                "tile_count_matches": True,
                "missing_from_dataset_count": 0,
                "extra_in_dataset_count": 0,
                "pending_post_dataset_missing_count": 0,
                "pending_post_dataset_extra_count": 0,
                "manifest": {"checked": True, "ok": True},
            },
        ),
    )
    with Session(engine) as session:
        summary = reliability_summary(session)

    assert summary["integrity"]["source_without_processed"] == 2
    assert summary["integrity"]["processed_without_source"] == 3
    assert summary["integrity"]["current_dataset_count"] == 1
    assert summary["integrity"]["critical_count"] == 0
    assert summary["system"]["status"] == "degraded"
    assert any("несвязанные" in reason for reason in summary["system"]["reasons"])


def test_retry_overdue_requires_unexecuted_deadline() -> None:
    from bankrotai.services.reliability import _retry_overdue

    now = utc_now()
    retry = now - timedelta(minutes=5)
    item = SimpleNamespace(
        next_retry_at=retry,
        last_attempt_at=now - timedelta(hours=1),
        last_success_at=now - timedelta(hours=1),
        status="healthy",
    )
    assert _retry_overdue(item, now)
    assert not _retry_overdue(item, now, paused=True)
    item.last_attempt_at = now - timedelta(minutes=1)
    assert not _retry_overdue(item, now)
    item.last_attempt_at = None
    item.last_success_at = now - timedelta(minutes=1)
    assert not _retry_overdue(item, now)


def test_retry_overdue_degrades_otherwise_fresh_source() -> None:
    item = SimpleNamespace(
        status="healthy", circuit_state="closed",
        freshness_status="fresh", coverage_status="fresh",
        last_error=None, last_failure_at=None, last_success_at=utc_now(),
    )
    assert _source_status(item) == "healthy"
    assert _source_status(item, retry_overdue=True) == "degraded"
    assert _source_status(item, retry_overdue=True, paused=True) == "paused"


def test_never_checked_source_is_not_a_stale_failure() -> None:
    item = SimpleNamespace(
        status="not_checked", circuit_state="closed",
        freshness_status="stale", coverage_status="missing",
        last_error=None, last_failure_at=None, last_attempt_at=None, last_success_at=None,
    )
    assert _source_status(item) == "not_checked"


def test_summary_critical_dates_and_host_fields_are_safe(monkeypatch) -> None:
    from bankrotai.services.reliability import reliability_summary

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "bankrotai.services.reliability.reliability_sources",
        lambda session: {
            "generated_at": utc_now().isoformat(),
            "configured_total": 2, "scheduled_total": 2, "paused_total": 1,
            "healthy": 1, "degraded": 0, "failed": 0, "not_checked": 0,
            "items": [],
        },
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability.geocoding_progress",
        lambda session: {"paused": False, "percent": 90.0, "total": 10, "geocoded": 9},
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability.operations_host_status",
        lambda session: {
            "backup_healthy": True, "runner_healthy": False,
            "disk": {"free_gb": 20.0, "checked_at": utc_now()},
            "backup": {"restore_verification": "passed", "size_bytes": 500, "private_token": "DO_NOT_LEAK"},
            "runner": {"checked_at": utc_now(), "secret_cookie": "DO_NOT_LEAK"},
            "maintenance": {"secret": "DO_NOT_LEAK"},
            "backup_age_hours": 1.0, "runner_age_seconds": 4_000,
        },
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability._current_quality",
        lambda session: {"population": 10, "percentages": {}},
    )
    monkeypatch.setattr(
        "bankrotai.services.reliability._heavy_quality_bundle",
        lambda session: (
            {
                "integrity": {
                    "source_without_processed": 0,
                    "processed_without_source": 0,
                    "active_and_archived_source_lots": 1,
                    "tile_count_mismatches": 0,
                    "current_dataset_count": 1,
                },
                "source_date_quality": {"first_seen_after_last_seen": 1},
            },
            {
                "ok": True,
                "dataset": {"version": "v1", "status": "ready", "declared_point_count": 10, "published_at": utc_now()},
                "point_count_matches": True, "tile_count_matches": True,
                "missing_from_dataset_count": 0, "extra_in_dataset_count": 0,
                "manifest": {"checked": True, "ok": True},
            },
        ),
    )
    with Session(engine) as session:
        summary = reliability_summary(session)

    assert summary["system"]["status"] == "incident"
    assert summary["integrity"]["active_and_archived"] == 1
    assert summary["integrity"]["date_invariants"] == 1
    assert summary["host"]["disk"]["status"] == "warning"
    assert summary["host"]["runner_diagnostic"]["status"] == "stale"
    assert summary["geo"]["scope_description_ru"]
    assert summary["generated_at"] == summary["system"]["generated_at"]
    assert summary["generated_at"] == summary["sources"]["generated_at"]
    assert "DO_NOT_LEAK" not in str(summary)
    assert "maintenance" not in summary["host"]


def test_reliability_message_redaction() -> None:
    from bankrotai.services.reliability import _safe_error

    error = "Request failed authorization: my-super-secret token=abc123?key=hi"
    redacted = _safe_error(error)
    assert redacted
    assert "my-super-secret" not in redacted
    assert "abc123" not in redacted
    assert "token=[скрыто]" in redacted
    assert _safe_error(None) is None


def test_paused_configured_source_keeps_denominator(monkeypatch) -> None:
    from bankrotai.db import AppSetting
    from bankrotai.services.reliability import reliability_sources

    monkeypatch.setattr(
        "bankrotai.services.ingestion.default_source_specs",
        lambda: [SimpleNamespace(source_id="torgi.gov.ru")],
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AppSetting(key="source_paused:ГИС Торги", value="true"))
        session.commit()
        result = reliability_sources(session)
    assert result["configured_total"] == 1
    assert result["scheduled_total"] == 1
    assert result["paused_total"] == 1
    rows = [item for item in result["items"] if item["canonical_id"] == "torgi.gov.ru"]
    assert len(rows) == 1
    assert rows[0]["configured"]
    assert rows[0]["paused"]
    assert rows[0]["overall_status"] == "paused"
