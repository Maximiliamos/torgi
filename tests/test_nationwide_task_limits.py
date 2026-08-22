from bankrotai.tasks import nationwide_lot_sync_task


def test_nationwide_ingestion_has_a_dedicated_bounded_runtime() -> None:
    assert nationwide_lot_sync_task.soft_time_limit == 6_900
    assert nationwide_lot_sync_task.time_limit == 7_200
