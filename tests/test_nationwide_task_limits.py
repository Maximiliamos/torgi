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
