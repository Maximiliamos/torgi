from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bankrotai.services.scheduled_sync_catchup import (
    decide_missed_full_run, most_recent_full_window,
)


@pytest.mark.parametrize("current,expected", [
    ("2026-11-04T05:30:00+00:00", "2026-11-03T18:00:00+00:00"),
    ("2026-11-04T06:30:00+00:00", "2026-11-04T06:00:00+00:00"),
    ("2026-11-04T17:55:00+00:00", "2026-11-04T06:00:00+00:00"),
    ("2026-11-04T18:40:00+00:00", "2026-11-04T18:00:00+00:00"),
])
def test_moscow_0900_2100_windows_are_unambiguous_utc(current, expected):
    assert most_recent_full_window(datetime.fromisoformat(current)) == datetime.fromisoformat(expected)


def test_refuse_naive_clock():
    with pytest.raises(ValueError, match="UTC-aware"):
        most_recent_full_window(datetime(2026, 11, 4, 6, 30))


def test_missing_0900_catches_up_only_once_after_grace():
    calls = []

    def queue():
        calls.append("queued")
        return "run-1"

    now = datetime(2026, 11, 4, 6, 11, tzinfo=timezone.utc)
    result = decide_missed_full_run(
        now=now, has_full_run_after=lambda _: False, enabled_source_count=2, schedule=queue
    )
    assert result["status"] == "queued"
    assert result["expected_window_utc"] == "2026-11-04T06:00:00+00:00"
    assert calls == ["queued"]


def test_never_repeat_a_full_run_even_if_previous_attempt_failed():
    calls = []
    result = decide_missed_full_run(
        now=datetime(2026, 11, 4, 6, 15, tzinfo=timezone.utc),
        has_full_run_after=lambda _: True,
        enabled_source_count=2,
        schedule=lambda: calls.append(1),
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "full_run_already_started"
    assert calls == []


def test_single_active_lease_failure_is_reported_without_second_run():
    from bankrotai.services.ingestion import SyncAlreadyRunningError

    def busy():
        raise SyncAlreadyRunningError("existing-run")
    result = decide_missed_full_run(
        now=datetime(2026, 11, 4, 6, 15, tzinfo=timezone.utc),
        has_full_run_after=lambda _: False, enabled_source_count=2, schedule=busy,
    )
    assert result["status"] == "busy"
    assert result["run_id"] == "existing-run"


def test_grace_window_and_all_paused_sources_do_not_queue():
    now = datetime(2026, 11, 4, 6, 2, tzinfo=timezone.utc)
    calls = []
    for count, expected in [(2, "deferred"), (0, "skipped")]:
        result = decide_missed_full_run(
            now=now, enabled_source_count=count, has_full_run_after=lambda _: False,
            schedule=lambda: calls.append(1),
        )
        assert result["status"] == expected
    assert calls == []


def test_recovery_is_registered_as_periodic_maintenance_not_second_full_cron():
    from bankrotai.tasks import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["recover-missed-scheduled-full"]["schedule"] == 900.0
    assert (schedule["recover-missed-scheduled-full"]["task"]
            == "bankrotai.tasks.recover_missed_full_source_sync_task")
    full = schedule["refresh-nationwide-sources-full"]
    assert full["schedule"].hour == {6, 18}
    assert full["schedule"].minute == {0}
