from bankrotai import tasks


def test_nationwide_ingestion_uses_progress_watchdog_with_emergency_ceiling() -> None:
    # Progress/lease health is the primary stall detector. The Celery limits
    # are deliberately much larger than a normal full reconciliation and only
    # serve as a final circuit breaker if the worker itself becomes wedged.
    assert tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS >= 4 * 60 * 60
    assert (
        tasks._DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS
        >= tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS + 60 * 60
    )
    assert tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS > tasks.settings.celery_soft_time_limit
    assert tasks._DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS > tasks.settings.celery_hard_time_limit

    for task in (
        tasks.nationwide_lot_sync_task,
        tasks.automatic_nationwide_lot_refresh_task,
        tasks.automatic_nationwide_source_retry_task,
    ):
        assert task.soft_time_limit == tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS
        assert task.time_limit == tasks._DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS



def test_tbankrot_is_always_isolated_from_automatic_source_sets(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "get_app_setting", lambda *_args, **_kwargs: "false")

    assert tasks._source_is_paused("tbankrot.ru") is True
    automatic = tasks._unpaused_source_specs(tasks.default_source_specs())
    assert "tbankrot.ru" not in {spec.source_id for spec in automatic}
    assert "tbankrot.ru" in {spec.source_id for spec in tasks.source_full_specs("tbankrot.ru")}



def test_deferred_tbankrot_sync_is_pinned_to_ingestion_queue(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    queued: list[dict] = []

    class Result:
        id = "deferred-tbankrot-1"

    monkeypatch.setattr(
        tasks.deferred_tbankrot_sync_task,
        "apply_async",
        lambda **kwargs: queued.append(kwargs) or Result(),
    )

    task_id = tasks.schedule_deferred_tbankrot_sync(triggered_by="test-auth")

    assert task_id == "deferred-tbankrot-1"
    assert queued == [{
        "args": ["test-auth"],
        "countdown": 15,
        "queue": tasks._QUEUE_INGESTION,
    }]
    assert tasks.celery_app.conf.task_routes["bankrotai.tasks.deferred_tbankrot_sync_task"] == {
        "queue": tasks._QUEUE_INGESTION,
    }
