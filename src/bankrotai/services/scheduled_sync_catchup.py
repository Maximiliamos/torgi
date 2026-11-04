"""Durable catch-up for a missed 09:00/21:00 Moscow Celery Beat full scan.

Celery crontab does not replay jobs missed while Home/Docker were offline.
This watchdog only schedules one *missing* window after a grace period. It
never circumvents the existing PostgreSQL single-active-source-sync lease.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def most_recent_full_window(now: datetime) -> datetime:
    """UTC counterpart of 09:00/21:00 Europe/Moscow (no DST)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("UTC-aware clock required")
    now = now.astimezone(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour >= 18:
        return day + timedelta(hours=18)
    if now.hour >= 6:
        return day + timedelta(hours=6)
    return day - timedelta(hours=6)  # yesterday at 18:00 UTC


def decide_missed_full_run(
    *,
    now: datetime,
    has_full_run_after: Callable[[datetime], bool],
    enabled_source_count: int,
    schedule: Callable[[], str],
    grace: timedelta = timedelta(minutes=10),
) -> dict[str, Any]:
    target = most_recent_full_window(now)
    if enabled_source_count <= 0:
        return {"status": "skipped", "reason": "all_sources_paused"}
    if now.astimezone(timezone.utc) < target + grace:
        return {"status": "deferred", "reason": "beat_grace_window"}
    if has_full_run_after(target):
        return {"status": "skipped", "reason": "full_run_already_started",
                "expected_window_utc": target.isoformat()}
    try:
        run_id = schedule()
    except Exception as exc:
        from bankrotai.services.ingestion import SyncAlreadyRunningError
        if isinstance(exc, SyncAlreadyRunningError):
            return {"status": "busy", "reason": "single_active_sync_lease",
                    "run_id": exc.run_id}
        raise
    return {"status": "queued", "run_id": run_id,
            "expected_window_utc": target.isoformat(), "trigger": "beat-recovery"}


def recover_missed_full_source_sync_task() -> dict[str, Any]:
    from sqlalchemy import select

    from bankrotai.db import LotSyncRun, SessionLocal
    from bankrotai.services.ingestion import default_source_specs
    from bankrotai.tasks import _unpaused_source_specs, schedule_nationwide_lot_sync

    now = datetime.now(timezone.utc)

    def has_run(target: datetime) -> bool:
        # Database UTC timestamps are stored naive. A completed, failed, queued
        # or running full attempt counts as an attempt; failed attempts must use
        # normal targeted retry policy, not generate infinite catch-up loops.
        with SessionLocal() as session:
            return session.scalar(
                select(LotSyncRun.id)
                .where(
                    LotSyncRun.started_at >= target.replace(tzinfo=None),
                    LotSyncRun.trigger_type.in_(("scheduled_full", "manual_full")),
                )
                .limit(1)
            ) is not None

    return decide_missed_full_run(
        now=now, has_full_run_after=has_run,
        enabled_source_count=len(_unpaused_source_specs(default_source_specs())),
        schedule=lambda: schedule_nationwide_lot_sync(
            triggered_by="beat-recovery", mode="full",
        ),
    )
