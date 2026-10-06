from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base
from bankrotai.services.operations_status import operations_host_status, record_operations_snapshot


def test_operations_host_status_combines_disk_backup_and_runner_snapshots() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 5, 19, 0, 0)

    with Session(engine) as session:
        record_operations_snapshot(
            session,
            "maintenance",
            {
                "checked_at": (now - timedelta(minutes=5)).isoformat(),
                "healthy": True,
                "disk_free_gb_after": 28.4,
            },
        )
        record_operations_snapshot(
            session,
            "backup",
            {
                "created_at": (now - timedelta(hours=2)).isoformat(),
                "restore_verification": "passed",
                "size_bytes": 123,
            },
        )
        record_operations_snapshot(
            session,
            "runner",
            {
                "checked_at": (now - timedelta(minutes=2)).isoformat(),
                "healthy": True,
                "failed_endpoint_count": 0,
            },
        )
        session.commit()

        status = operations_host_status(session, now=now)

    assert status["disk"]["healthy"] is True
    assert status["disk"]["free_gb"] == 28.4
    assert status["backup_healthy"] is True
    assert status["backup_age_hours"] == 2.0
    assert status["runner_healthy"] is True
    assert status["runner_age_seconds"] == 120.0


def test_operations_host_status_marks_stale_or_low_headroom_snapshots_unhealthy() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 5, 19, 0, 0)

    with Session(engine) as session:
        record_operations_snapshot(
            session,
            "maintenance",
            {"checked_at": now.isoformat(), "disk_free_gb_after": 12.0},
        )
        record_operations_snapshot(
            session,
            "backup",
            {
                "created_at": (now - timedelta(hours=61)).isoformat(),
                "restore_verification": "passed",
            },
        )
        record_operations_snapshot(
            session,
            "runner",
            {
                "checked_at": (now - timedelta(hours=2)).isoformat(),
                "healthy": True,
            },
        )
        session.commit()

        status = operations_host_status(session, now=now)

    assert status["disk"]["healthy"] is False
    assert status["backup_healthy"] is False
    assert status["runner_healthy"] is False
