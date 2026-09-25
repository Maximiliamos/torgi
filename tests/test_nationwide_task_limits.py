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



def test_scheduled_refresh_requires_recent_complete_snapshot_for_every_source(monkeypatch) -> None:
    from contextlib import contextmanager
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    import bankrotai.tasks as tasks
    from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun
    from bankrotai.services.ingestion import default_source_specs

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = tasks._utc_now()

    with Session(engine) as session:
        for index, spec in enumerate(default_source_specs()):
            run_id = f"complete-{index}"
            session.add(
                LotSyncRun(
                    id=run_id,
                    triggered_by="test",
                    trigger_type="scheduled_full",
                    status="success",
                    total_sources=1,
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                )
            )
            session.flush()
            session.add(
                LotSyncSourceRun(
                    sync_run_id=run_id,
                    source_system=spec.source_id,
                    status="success",
                    complete_source_run=True,
                    items_seen=10,
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                )
            )
        session.commit()

    @contextmanager
    def test_scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(tasks, "session_scope", test_scope)
    assert tasks._scheduled_nationwide_sync_mode() == "fast"

    with Session(engine) as session:
        stale_source = default_source_specs()[0].source_id
        for row in session.query(LotSyncSourceRun).filter_by(source_system=stale_source):
            row.finished_at = now - timedelta(hours=31)
        session.commit()

    assert tasks._scheduled_nationwide_sync_mode() == "full"
