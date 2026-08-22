from bankrotai.tasks import nationwide_lot_sync_task


def test_nationwide_ingestion_uses_normal_bounded_runtime() -> None:
    assert nationwide_lot_sync_task.soft_time_limit == 1_500
    assert nationwide_lot_sync_task.time_limit == 1_800
