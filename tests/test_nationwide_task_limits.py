from bankrotai.tasks import nationwide_lot_sync_task


def test_nationwide_ingestion_uses_normal_bounded_runtime() -> None:
    assert nationwide_lot_sync_task.soft_time_limit == 1_500
    assert nationwide_lot_sync_task.time_limit == 1_800



def test_nationwide_refresh_is_scheduled_hourly() -> None:
    from bankrotai.tasks import celery_app

    schedule = celery_app.conf.beat_schedule["nationwide-source-refresh"]
    assert schedule["task"] == "bankrotai.tasks.scheduled_nationwide_refresh_task"
    assert schedule["schedule"] == 3600.0
    assert schedule["options"]["expires"] == 3300


def test_scheduled_refresh_reports_existing_run_as_busy(monkeypatch) -> None:
    import bankrotai.tasks as tasks
    from bankrotai.services.ingestion import SyncAlreadyRunningError

    monkeypatch.setattr(tasks, "_scheduled_nationwide_sync_mode", lambda: "fast")

    def already_running(**_kwargs):
        raise SyncAlreadyRunningError("existing-run")

    monkeypatch.setattr(tasks, "schedule_nationwide_lot_sync", already_running)

    assert tasks.scheduled_nationwide_refresh_task.run() == {
        "status": "busy",
        "mode": "fast",
        "run_id": "existing-run",
    }
