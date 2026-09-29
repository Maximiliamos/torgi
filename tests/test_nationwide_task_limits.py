from bankrotai import tasks


def test_nationwide_ingestion_uses_progress_watchdog_not_wall_clock_kill_switch() -> None:
    # Durable nationwide runs are bounded by lease/progress health, not by a
    # guessed elapsed runtime. HTTP/DB operations keep their own bounded timeouts.
    for task in (
        tasks.nationwide_lot_sync_task,
        tasks.automatic_nationwide_lot_refresh_task,
        tasks.automatic_nationwide_source_retry_task,
    ):
        assert task.soft_time_limit == 0
        assert task.time_limit == 0
