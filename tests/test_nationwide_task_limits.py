from bankrotai import tasks


def test_nationwide_ingestion_uses_dedicated_bounded_runtime() -> None:
    assert tasks.settings.celery_nationwide_soft_time_limit == 3_300
    assert tasks.settings.celery_nationwide_hard_time_limit == 3_600
    assert tasks.settings.celery_nationwide_soft_time_limit > tasks.settings.celery_soft_time_limit
    assert tasks.settings.celery_nationwide_hard_time_limit > tasks.settings.celery_nationwide_soft_time_limit

    for task in (
        tasks.nationwide_lot_sync_task,
        tasks.automatic_nationwide_lot_refresh_task,
        tasks.automatic_nationwide_source_retry_task,
    ):
        assert task.soft_time_limit == tasks.settings.celery_nationwide_soft_time_limit
        assert task.time_limit == tasks.settings.celery_nationwide_hard_time_limit
